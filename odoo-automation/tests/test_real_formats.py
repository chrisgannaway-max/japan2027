"""Parsers + mappings against layout text extracted from the real sample reports
(staff names redacted).  Every entry must balance to the cent."""
from datetime import date
from decimal import Decimal

import pytest

from pathlib import Path

from conftest import FIXTURES
from pms_to_odoo.mapping import GLMapping
from pms_to_odoo.parsers import detect_pms, get_parser, read_text

CONFIG = FIXTURES.parent.parent / "config" / "gl_mapping"


def by_label(report):
    d = {}
    for l in report.lines:
        d.setdefault(l.label, l)
    return d


# ------------------------------------------------------------------ Hilton PEP
@pytest.fixture(scope="module")
def pep():
    return get_parser("PEP").parse(FIXTURES / "pep_final_audit.txt", "OKCON")


def test_pep_header_and_detection(pep):
    assert detect_pms(read_text(FIXTURES / "pep_final_audit.txt")) == "PEP"
    assert pep.business_date == date(2025, 11, 10)          # "Date : Nov 10, 2025", not the run date
    assert pep.pms_property_id == "OKCON"
    assert pep.property_name.startswith("Embassy Suites by Hilton")
    assert pep.warnings == []


def test_pep_uses_net_today_column_and_sections(pep):
    d = by_label(pep)
    assert d["GUEST ROOM"].amount == Decimal("31222.62") and d["GUEST ROOM"].section == "revenue"
    assert d["NO SHOW ROOM REVENUE"].amount == Decimal("-1619.01")   # Actual 0 + Adjusted -1,619.01
    assert d["MASTER"].amount == Decimal("34622.46") and d["MASTER"].section == "settlement"  # net of $134.06 adj
    assert d["OCCUPANCY TAX"].amount == Decimal("2976.81") and d["OCCUPANCY TAX"].section == "tax"
    assert d["100% GTD RESTAURANT ALLOWANCE"].section == "revenue"       # wrapped label re-joined
    assert d["BALANCE FORWARD DIRECT BILL PAYMENT"].section == "settlement"
    # cash-control pages are not payments
    assert "CASH DROPS" not in d and "CALCULATED DEPOSIT" not in d and "IN HOUSE" not in d


def test_pep_totals_match_report_and_ledger(pep):
    assert pep.total("revenue") == Decimal("79829.99") == pep.stats["Total Revenues (Net Today)"]
    assert pep.total("tax") == Decimal("6350.07") == pep.stats["Total Taxes (Net Today)"]
    assert pep.total("settlement") == Decimal("47194.90") == pep.stats["Total Payments (Net Today)"]
    assert pep.total("ledger") == Decimal("38985.16") == pep.stats["Hotel Balance Net Change"]
    assert pep.stats["Rooms Sold"] == Decimal("212")
    assert pep.stats["ADR"] == Decimal("147.83")


def test_pep_entry_balances_with_example_mapping(pep):
    entry = GLMapping.load(CONFIG / "hilton_pep.example.yaml").build_entry(pep)
    assert entry.imbalance == 0
    # 47,194.90 payments + 43,756.87 ledger increases + 1,619.01 no-show reversal (negative revenue -> debit)
    assert entry.total_debit == Decimal("92570.78")
    assert entry.ref == "PEP-OKCON-2025-11-10"
    assert any(l.name == "Total Closed Folio Net Change" and l.debit == Decimal("315.23") for l in entry.lines)


# ------------------------------------------------------------------ choiceADVANTAGE
@pytest.fixture(scope="module")
def choice():
    return get_parser("CHOICEADV").parse(FIXTURES / "choice_night_audit.txt", "TXI47")


def test_choice_parses_journal_summary(choice):
    assert detect_pms(read_text(FIXTURES / "choice_night_audit.txt")) == "CHOICEADV"
    assert choice.business_date == date(2025, 11, 11)
    assert choice.pms_property_id == "TXI47"
    d = by_label(choice)
    assert d["Room Charge"].code == "RM" and d["Room Charge"].amount == Decimal("1785.45")
    assert d["Master Card"].section == "settlement" and d["Master Card"].amount == Decimal("1642.90")
    assert d["State Tax"].section == "tax"
    assert d["Direct Bill"].section == "transfer" and d["Applied Credit"].section == "transfer"
    assert d["Guest Ledger Net Change"].amount == Decimal("253.98")
    assert d["AR Ledger Net Change"].amount == Decimal("-499.10")
    assert choice.stats["Occ% of Total Available Rooms"] == Decimal("42.86")
    assert choice.warnings == []


def test_choice_entry_balances(choice):
    entry = GLMapping.load(CONFIG / "choice_advantage.example.yaml").build_entry(choice)
    assert entry.imbalance == 0
    assert entry.total_debit == Decimal("2552.37")
    assert not any("Direct Bill" in l.name for l in entry.lines)      # transfers not booked


# ------------------------------------------------------------------ HotelKey
@pytest.fixture(scope="module")
def hotelkey():
    return get_parser("HOTELKEY").parse(FIXTURES / "hotelkey_trial_balance.txt", "OKCMD")


def test_hotelkey_rows_and_wrapped_names(hotelkey):
    assert detect_pms(read_text(FIXTURES / "hotelkey_trial_balance.txt")) == "HOTELKEY"
    assert hotelkey.business_date == date(2025, 9, 30)
    assert hotelkey.pms_property_id == "OKCMD"
    d = by_label(hotelkey)
    assert d["VISA"].amount == Decimal("4342.44") and d["VISA"].section == "settlement"   # sign flipped
    assert d["AMEX"].amount == Decimal("-20.02")                                          # net refund
    assert d["CASH"].amount == Decimal("41.00")
    assert d["GUEST ROOM"].amount == Decimal("8943.24") and d["GUEST ROOM"].code == "RR"
    assert d["City Tax"].amount == Decimal("365.52") and d["City Tax"].section == "tax"
    assert d["reservation_in_house(Offset)"].section == "ledger"
    assert d["group_in_house(Offset)"].amount == Decimal("3605.91")
    assert d["ar(Offset)"].amount == Decimal("-419.64")
    assert "IHG One Rewards Reimbursement" in d
    assert "Loyalty Reward Reconciliation Adjustment" in d
    assert "Function_Meeting Afternoon Tea - Function Room Rental _ Setup Revenue" in d
    assert "LATE CHECK OUT FEE" in d and "NO SHOW ROOM REVENUE" in d


def test_hotelkey_totals_and_balance(hotelkey):
    assert hotelkey.total("revenue") == Decimal("8998.92")
    assert hotelkey.total("tax") == Decimal("1576.76")
    assert hotelkey.total("settlement") == Decimal("5602.07")
    assert hotelkey.total("ledger") == Decimal("4973.61")
    assert hotelkey.stats["Trial Balance Debit"] == hotelkey.stats["Trial Balance Credit"] == Decimal("11191.64")
    entry = GLMapping.load(CONFIG / "ihg_hotelkey.example.yaml").build_entry(hotelkey)
    assert entry.imbalance == 0
    assert entry.total_credit == Decimal("11015.34")   # 8,998.92 rev + 1,576.76 tax + 419.64 AR decrease + 20.02 Amex refund


# ------------------------------------------------------------------ OPERA
@pytest.fixture(scope="module")
def opera():
    return get_parser("OPERA").parse(FIXTURES / "opera_trial_balance.txt", "CANDLEWOOD-MOORE")


def test_opera_rows(opera):
    assert detect_pms(read_text(FIXTURES / "opera_trial_balance.txt")) == "OPERA"
    assert opera.business_date == date(2025, 11, 9)                  # report date, not run date 11-10-25
    assert opera.property_name == "Candlewood Suites Moore Oklahoma"
    d = by_label(opera)
    assert d["Accommodation"].code == "1000" and d["Accommodation"].amount == Decimal("1546.10")
    assert d["State Tax - Room"].section == "tax"
    assert d["MasterCard"].amount == Decimal("12291.60") and d["MasterCard"].section == "settlement"
    assert d["Direct Billing/City Ledger"].section == "transfer"
    assert d["Guest Ledger Net Change"].amount == Decimal("-11532.37")
    assert d["AR Ledger Net Change"].amount == Decimal("64.06")
    assert d["Deposit Ledger Net Change"].amount == Decimal("0")
    assert opera.stats["Transaction Total Today"] == Decimal("-11532.37")


def test_opera_entry_balances(opera):
    entry = GLMapping.load(CONFIG / "ihg_opera.example.yaml").build_entry(opera)
    assert entry.imbalance == 0
    assert entry.total_debit == Decimal("13377.50")


# ------------------------------------------------------------------ real PDFs when present
SAMPLES = FIXTURES.parent.parent / "samples"


@pytest.mark.skipif(not (SAMPLES / "OKCON_2025-11-10_FinalAudit.pdf").exists(), reason="sample PDFs not present")
def test_pdf_extraction_matches_fixture_totals():
    pep_pdf = get_parser("PEP").parse(SAMPLES / "OKCON_2025-11-10_FinalAudit.pdf", "OKCON")
    assert pep_pdf.total("settlement") == Decimal("47194.90")
    hk = get_parser("HOTELKEY").parse(SAMPLES / "OKCMD_2025-09-30_TrialBalance.eml", "OKCMD")   # via .eml
    assert hk.total("revenue") == Decimal("8998.92")
    ch = get_parser("CHOICEADV").parse(SAMPLES / "TXI47_2025-11-11_ChoiceAdvantage.pdf", "TXI47")
    assert ch.total("ledger") == Decimal("-245.12")
    op = get_parser("OPERA").parse(SAMPLES / "CANDLEWOOD-MOORE_2025-11-09_OperaTrialBalance.pdf", "X")
    assert op.total("revenue") == Decimal("1629.74")


# ------------------------------------------------------------------ Agilysys (Marriott)
@pytest.fixture(scope="module")
def agilysys():
    return get_parser("AGILYSYS").parse(FIXTURES / "agilysys_ledger_summary.txt", "OKCAW")


def test_agilysys_rows(agilysys):
    assert detect_pms(read_text(FIXTURES / "agilysys_ledger_summary.txt")) == "AGILYSYS"
    assert agilysys.business_date == date(2026, 7, 8)             # Start Date, not the print date 07/09
    assert agilysys.property_name == "SpringHill Suites By Marriott Oklahoma City Airport West"
    assert agilysys.warnings == []
    rows = [(l.section, l.code, l.label, l.amount) for l in agilysys.lines]
    assert ("settlement", "MC", "MasterCard", Decimal("597.48")) in rows           # city ledger payment, sign flipped
    assert ("settlement", "VI", "Visa", Decimal("2540.23")) in rows                # guest ledger payment
    assert ("settlement", "BV", "Marriott Bonvoy Redemption", Decimal("45.98")) in rows
    assert ("revenue", "RP", "Room Charge-Pleas Trans", Decimal("7089.23")) in rows
    assert ("revenue", "C3", "Guestroom Cancellations", Decimal("-108.30")) in rows
    assert ("tax", "T11E", "Occupancy Sales Tax 9.25% Ex", Decimal("456.25")) in rows   # code glued to label
    assert ("tax", "T21E", "State Occupancy Tax 4.5% Ex", Decimal("240.74")) in rows
    assert ("ledger", "", "City Ledger Net Change", Decimal("-679.66")) in rows         # 351.49 -> (328.17)
    assert ("ledger", "", "Deposit Ledger Net Change", Decimal("456.18")) in rows       # (456.18) -> 0.00
    assert ("ledger", "", "Guest Ledger Net Change", Decimal("3313.01")) in rows        # 23,190.57 -> 26,503.58
    assert all(l.section == "transfer" for l in agilysys.lines if "Transfer" in l.label)
    assert agilysys.total("transfer") == 0
    assert agilysys.stats["Guest Ledger Revenue Total"] == Decimal("8027.90")


def test_agilysys_totals_and_balance(agilysys):
    assert agilysys.total("revenue") + agilysys.total("tax") == Decimal("7944.72")
    assert agilysys.total("settlement") == Decimal("4855.19")
    assert agilysys.total("ledger") == Decimal("3089.53")
    entry = GLMapping.load(CONFIG / "marriott_agilysys.example.yaml").build_entry(agilysys)
    assert entry.imbalance == 0
    assert entry.ref == "AGILYSYS-OKCAW-2026-07-08"


# ------------------------------------------------------------------ SynXis (Wyndham)
SYNXIS = FIXTURES / "synxis"


@pytest.fixture(scope="module")
def synxis():
    return get_parser("SYNXIS").parse(SYNXIS / "transaction_totals_summary.txt", "LQ89051")


def test_synxis_merges_companion_and_parses_rows(synxis):
    assert detect_pms(read_text(SYNXIS / "transaction_totals_summary.txt")) == "SYNXIS"
    assert detect_pms(read_text(SYNXIS / "hotel_ledger_compare.txt")) == "SYNXIS"
    assert synxis.business_date == date(2025, 11, 11)                 # "For Yesterday (11 Nov 2025)"
    assert synxis.pms_property_id == "89051"
    assert synxis.property_name == "La Quinta Inn & Suites by Wyndham Oklahoma City Airport"
    assert [Path(c).name for c in synxis.companions] == ["hotel_ledger_compare.txt"]
    assert synxis.warnings == []
    rows = [(l.section, l.code, l.label, l.amount) for l in synxis.lines]
    assert ("revenue", "RM", "ROOM CHARGE", Decimal("4789.06")) in rows
    assert ("revenue", "NS", "NO SHOW CHARGE", Decimal("72.00")) in rows       # description wraps two cells
    assert ("revenue", "MARKET", "MARKET", Decimal("69.28")) in rows
    assert ("tax", "1001", "State Tax 8.625%", Decimal("420.54")) in rows       # first row carries the date
    assert ("tax", "5002", "Package tax for market", Decimal("2.85")) in rows
    assert ("settlement", "MC", "MASTER CARD", Decimal("1825.61")) in rows      # sign flipped
    assert ("settlement", "CA", "CASH", Decimal("33.22")) in rows
    assert ("transfer", "DR", "DIRECT BILL", Decimal("0")) in rows
    assert ("ledger", "", "Guest Ledger Net Change", Decimal("2174.66")) in rows
    assert ("ledger", "", "AR Ledger Net Change", Decimal("260.06")) in rows
    assert ("ledger", "", "Group Ledger Net Change", Decimal("0")) in rows
    assert synxis.stats["Transactions Grand Total"] == Decimal("2434.72") == synxis.stats["Ledgers Grand Total Difference"]


def test_synxis_totals_and_balance(synxis):
    assert synxis.total("revenue") == Decimal("5030.34")
    assert synxis.total("tax") == Decimal("873.08")
    assert synxis.total("settlement") == Decimal("3468.70")
    assert synxis.total("ledger") == Decimal("2434.72")
    entry = GLMapping.load(CONFIG / "wyndham_synxis.example.yaml").build_entry(synxis)
    assert entry.imbalance == 0
    assert entry.ref == "SYNXIS-LQ89051-2025-11-11"


def test_synxis_ledger_file_as_entry_point_and_missing_companion(tmp_path):
    # starting from the ledger report finds the transaction report next to it
    r = get_parser("SYNXIS").parse(SYNXIS / "hotel_ledger_compare.txt", "LQ89051")
    assert r.total("revenue") == Decimal("5030.34") and r.total("ledger") == Decimal("2434.72")
    # alone in a folder, the transaction report warns that the ledger movements are missing
    alone = tmp_path / "transaction_totals_summary.txt"
    alone.write_text((SYNXIS / "transaction_totals_summary.txt").read_text())
    r2 = get_parser("SYNXIS").parse(alone, "LQ89051")
    assert r2.total("ledger") == 0 and any("Ledger Comparison" in w for w in r2.warnings)


def test_agilysys_csv_export_matches_pdf(agilysys):
    """The CSV export (what the property calls 'the Excel') must give the same lines as the PDF."""
    csv_rep = get_parser("AGILYSYS").parse(FIXTURES / "agilysys" / "Ledger_Summary_OKCAW_2026-07-09_10-47-16.csv", "OKCAW")
    assert detect_pms(read_text(FIXTURES / "agilysys" / "Ledger_Summary_OKCAW_2026-07-09_10-47-16.csv")) == "AGILYSYS"
    assert csv_rep.business_date == date(2026, 7, 8) and csv_rep.pms_property_id == "OKCAW"
    assert csv_rep.warnings == []
    key = lambda r: sorted((l.section, l.code, l.label, l.amount) for l in r.lines)  # noqa: E731
    assert key(csv_rep) == key(agilysys)
    entry = GLMapping.load(CONFIG / "marriott_agilysys.example.yaml").build_entry(csv_rep)
    assert entry.imbalance == 0 and entry.total_debit == Decimal("8739.56")


def test_agilysys_csv_without_subtotals_warns():
    r = get_parser("AGILYSYS").parse(FIXTURES / "agilysys" / "Ledger_Summary_OKCAW_2026-07-09_10-47-13_detail_only.csv", "OKCAW")
    assert r.total("revenue") == Decimal("7021.95") and r.total("ledger") == 0   # 7,944.72 less 922.77 tax
    assert any("ledger balance rows" in w for w in r.warnings)
