from datetime import date
from decimal import Decimal

from conftest import FIXTURES
from pms_to_odoo.parsers import get_parser
from pms_to_odoo.parsers.base import find_date, parse_amount


def test_parse_amount_formats():
    assert parse_amount("1,234.56") == Decimal("1234.56")
    assert parse_amount("(1,234.56)") == Decimal("-1234.56")
    assert parse_amount("-40.00") == Decimal("-40.00")
    assert parse_amount("$99.10") == Decimal("99.10")
    assert parse_amount("123.45-") == Decimal("-123.45")
    assert parse_amount("50.00CR") == Decimal("-50.00")
    assert parse_amount("Room Revenue") is None


def test_find_date_prefers_business_date_label():
    text = "Printed 09/14/2026\nBusiness Date: 09/13/2026"
    assert find_date(text) == date(2026, 9, 13)


def test_generic_sample_parses_sections_and_today_column():
    report = get_parser("GENERIC").parse(FIXTURES / "generic_daily_sample.txt", "HGI-EXAMPLE")
    assert report.pms == "GENERIC"
    assert report.business_date == date(2026, 9, 13)
    by_label = {l.label: l for l in report.lines}
    assert by_label["Room Revenue"].amount == Decimal("12480.00")
    assert by_label["Room Revenue"].section == "revenue"
    assert by_label["Paid Outs"].amount == Decimal("-40.00")
    assert by_label["State Sales Tax"].section == "tax"
    assert by_label["Visa/MC"].section == "settlement"
    assert by_label["Visa/MC"].amount == Decimal("8914.22")
    assert by_label["Guest Ledger Net Change"].section == "ledger"
    # statistics go to .stats, not .lines
    assert "Rooms Sold" in report.stats and report.stats["Occupancy %"] == Decimal("80.00")
    assert "Occupancy %" not in by_label
    # footer skipped
    assert not any("Page" in l.label for l in report.lines)


def test_brand_aliases():
    assert get_parser("hilton").pms == "PEP"
    assert get_parser("Choice Advantage").pms == "CHOICEADV"
    assert get_parser("wyndham").pms == "SYNXIS"
