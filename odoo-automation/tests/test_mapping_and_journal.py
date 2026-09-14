from datetime import date
from decimal import Decimal

import pytest

from conftest import FIXTURES
from fake_odoo import FakeTransport
from pms_to_odoo.journal import entry_to_odoo_values, format_entry, post_entry
from pms_to_odoo.mapping import GLMapping, MappingError
from pms_to_odoo.models import DailyReport, ReportLine
from pms_to_odoo.odoo_client import OdooClient
from pms_to_odoo.parsers import get_parser


@pytest.fixture
def report():
    return get_parser("GENERIC").parse(FIXTURES / "generic_daily_sample.txt", "HGI-EXAMPLE")


@pytest.fixture
def mapping():
    return GLMapping.load(FIXTURES / "generic_mapping.yaml")


def test_entry_balances_and_has_expected_sides(report, mapping):
    entry = mapping.build_entry(report)
    assert entry.ref == "GENERIC-HGI-EXAMPLE-2026-09-13"
    assert entry.imbalance == 0
    # credits: gross revenue 13,302.75 + tax 1,403.16 = 14,705.91 ; debits: payments 14,243.91 + paid outs 40 + adjustments 89 + ledger 333
    assert entry.total_credit == Decimal("14705.91")
    assert entry.total_debit == Decimal("14705.91")
    by_acct = {}
    for l in entry.lines:
        by_acct.setdefault(l.account_code, []).append(l)
    assert by_acct["4000"][0].credit == Decimal("12480.00")
    # negative revenue-side items become debits
    assert by_acct["4900"][0].debit == Decimal("40.00")
    assert by_acct["1120"][0].debit == Decimal("8914.22")
    assert by_acct["1210"][0].debit == Decimal("333.00")
    assert by_acct["1200"][0].partner_ref == "Direct Bill"
    assert all(l.analytic_code == "HGI-EXAMPLE" for l in entry.lines)
    assert "OUT OF BALANCE" not in format_entry(entry)


def test_unmapped_line_fails_loudly(mapping):
    r = DailyReport("X", date(2026, 1, 1), "PEP", [ReportLine("Spa Revenue", Decimal("10"))])
    with pytest.raises(MappingError, match="Spa Revenue"):
        mapping.build_entry(r)


def test_unbalanced_report_fails(mapping):
    r = DailyReport("X", date(2026, 1, 1), "PEP", [
        ReportLine("Room Revenue", Decimal("100")), ReportLine("Cash", Decimal("90"))])
    with pytest.raises(MappingError, match="does not balance"):
        mapping.build_entry(r)


def test_suspense_absorbs_unmapped(tmp_path):
    (tmp_path / "m.yaml").write_text(
        "journal: NA\nsuspense_account: '9999'\nrules:\n"
        "  - {match: '^room revenue$', account: '4000', side: credit}\n"
        "  - {match: '^cash$', account: '1010', side: debit}\n")
    m = GLMapping.load(tmp_path / "m.yaml")
    r = DailyReport("X", date(2026, 1, 1), "PEP", [
        ReportLine("Room Revenue", Decimal("100")), ReportLine("Cash", Decimal("90")),
        ReportLine("Mystery", Decimal("10"))])
    entry = m.build_entry(r)
    assert entry.imbalance == 0
    assert any(l.account_code == "9999" and l.name.startswith("UNMAPPED") for l in entry.lines)


def test_odoo_payload_and_idempotent_post(report, mapping):
    t = FakeTransport()
    client = OdooClient(t)
    entry = mapping.build_entry(report)
    values = entry_to_odoo_values(entry, client)
    assert values["move_type"] == "entry" and values["journal_id"] == 7 and values["company_id"] == 1
    assert values["date"] == "2026-09-13" and values["ref"] == entry.ref
    first = values["line_ids"][0]
    assert first[:2] == [0, 0] and first[2]["account_id"] == 10  # code 4000
    assert first[2]["analytic_distribution"] == {"50": 100}
    direct_bill = next(c[2] for c in values["line_ids"] if c[2]["name"] == "Direct Bill")
    assert direct_bill["partner_id"] == 60

    r1 = post_entry(entry, client, post=True)
    assert r1.status == "posted" and r1.move_id is not None
    assert t.records["account.move"][0]["state"] == "posted"
    r2 = post_entry(entry, client)
    assert r2.status == "exists" and r2.move_id == r1.move_id
    assert len(t.records["account.move"]) == 1

    dry = post_entry(entry, client, dry_run=True)
    assert dry.status == "dry-run"
