"""Wyndham - SynXis Property Hub (sph*) night-audit reports.

Two reports are needed together (sample: La Quinta OKC Airport, hotel 89051):

* **Transaction Totals Summary** (sphTransactionTotalSummary) - one row per charge code:
      Date  Charge Code  Description  GL Account  Base Type  #of Posting  Total
  Base Type is Taxes / CreditCard / Cash / DirectBill / RoomCharge / Other ...
  Charges positive, payments negative.  Grand Total == change in all ledgers.
* **Hotel Ledger Comparison Report** (sphHotelLedgerCompare) - balances per ledger
  (Guest, Group, AR, House) for yesterday and today with the Difference column.

Give the parser either file; it looks in the same folder for the companion report of the
same hotel and business date and merges it.  Without the ledger report the entry cannot
balance, so a warning is raised.  DIRECT BILL postings are transfers to the AR ledger and
are not booked.  The GL Account column is empty on the sample; if the property fills it in
SynXis, mapping can use it directly.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..models import DailyReport
from .base import BaseParser, collapse, iter_lines, parse_date, read_text, split_row

LEDGER_HEADERS = ("Guest Ledger", "Group Ledger", "AR Ledger", "House Ledger", "Deposit Ledger",
                  "Advance Deposit Ledger", "City Ledger")
ROW_RE = re.compile(
    r"^(?:[A-Za-z]{3} \d{1,2}, \d{4}\s+)?(?P<code>\S+)\s+(?P<desc>.+?)\s+(?:(?P<gl>\d{3,10})\s+)?"
    r"(?P<base>[A-Za-z]+)\s+(?P<n>\d+)\s+(?P<total>-?[\d,]+\.\d{2})$")
BASE_SECTION = {"taxes": "tax", "tax": "tax", "creditcard": "settlement", "cash": "settlement",
                "check": "settlement", "payment": "settlement", "directbill": "transfer",
                "roomcharge": "revenue", "other": "revenue", "package": "revenue", "fnb": "revenue"}

_TEXT_CACHE: dict[str, str] = {}


def _text(p: Path) -> str:
    key = str(p.resolve())
    if key not in _TEXT_CACHE:
        try:
            _TEXT_CACHE[key] = read_text(p)
        except Exception:  # noqa: BLE001 - unreadable neighbour files are simply skipped
            _TEXT_CACHE[key] = ""
    return _TEXT_CACHE[key]


def report_kind(text: str) -> Optional[str]:
    low = collapse(text).lower()
    if "transaction totals summary" in low:
        return "transactions"
    if "hotel ledger comparison" in low:
        return "ledgers"
    return None


def _hotel_and_date(text: str) -> tuple[str, str, Optional[date]]:
    flat = collapse(re.sub(r"===== (PAGE|ATTACHMENT)[^\n]*", " ", text))
    hm = re.search(r"Hotel:\s*(.+?)\s*\((\d{3,8})\)", flat)
    dm = re.search(r"(?:For|for)\s*:?\s*\w+\s*\((\d{1,2} [A-Za-z]{3} \d{4})\)", flat)
    return (collapse(hm.group(1)) if hm else "", hm.group(2) if hm else "",
            parse_date(dm.group(1)) if dm else None)


class WyndhamSynxisParser(BaseParser):
    pms = "SYNXIS"
    brand = "Wyndham"
    expects = "SynXis 'Transaction Totals Summary' + 'Hotel Ledger Comparison Report' (same folder)"

    def parse(self, path: str | Path, property_code: str,
              business_date: Optional[date] = None) -> DailyReport:
        p = Path(path)
        text = _text(p)
        kind = report_kind(text)
        name, hotel_id, d = _hotel_and_date(text)
        report = DailyReport(property_code=property_code, pms=self.pms,
                             business_date=business_date or d or date.today(), source_file=str(p),
                             pms_property_id=hotel_id, property_name=name)
        if kind is None:
            report.warnings.append("This does not look like a SynXis Transaction Totals Summary or Ledger Comparison.")
            report.recognised = False
            return report

        files = {kind: p}
        other = "ledgers" if kind == "transactions" else "transactions"
        companion = self._find_companion(p, other, hotel_id, report.business_date)
        if companion:
            files[other] = companion
            report.companions.append(str(companion))
        if "transactions" in files:
            self._parse_transactions(_text(files["transactions"]), files["transactions"].name, report)
        else:
            report.warnings.append("Transaction Totals Summary not found next to this file: no revenue/payment lines.")
        if "ledgers" in files:
            self._parse_ledgers(_text(files["ledgers"]), files["ledgers"].name, report)
        else:
            report.warnings.append("Hotel Ledger Comparison Report not found next to this file: "
                                   "ledger movements missing, the entry will not balance.")
        gt, lg = report.stats.get("Transactions Grand Total"), report.stats.get("Ledgers Grand Total Difference")
        if gt is not None and lg is not None and abs(gt - lg) > Decimal("0.01"):
            report.warnings.append(f"Transaction total ({gt}) differs from ledger movement ({lg}).")
        return report

    # ------------------------------------------------------------------ pieces
    @staticmethod
    def _find_companion(p: Path, kind: str, hotel_id: str, d: date) -> Optional[Path]:
        for cand in sorted(p.parent.iterdir()):
            if cand == p or not cand.is_file() or cand.suffix.lower() not in (".pdf", ".txt"):
                continue
            txt = _text(cand)
            if report_kind(txt) != kind:
                continue
            c_name, c_id, c_date = _hotel_and_date(txt)
            if (not hotel_id or c_id == hotel_id) and (c_date is None or c_date == d):
                return cand
        return None

    def _parse_transactions(self, text: str, fname: str, report: DailyReport) -> None:
        for n, line in iter_lines(text):
            c = collapse(line)
            lc = c.lower()
            if lc.startswith(("transaction totals", "currency", "hotel:", "date charge code", "report criteria",
                              "date range", "start date", "end date", "base type", "charge code", "gl account",
                              "group by", "user name", "report execution")):
                continue
            if lc.startswith("grand total") or re.match(r"[a-z]{3} \d{1,2}, \d{4} - total", lc):
                label, nums = split_row(line)
                if nums:
                    report.stats["Transactions Grand Total" if lc.startswith("grand") else f"Transactions Total {label}"] = nums[-1]
                continue
            m = ROW_RE.match(c)
            if not m:
                continue
            code, desc, base, total = m.group("code"), collapse(m.group("desc")), m.group("base"), Decimal(m.group("total").replace(",", ""))
            section = BASE_SECTION.get(base.lower())
            if section is None:
                section = "settlement" if (total < 0 or re.search(r"card|cash|check|payment", base, re.I)) else "revenue"
            amount = -total if section == "settlement" else total
            line_obj = report.add(desc, amount, section, f"{self.pms}:{fname}:L{n}", code=code)
            if m.group("gl"):
                line_obj.gl_code = m.group("gl")
            report.stats.setdefault("Transaction rows", Decimal("0"))
            report.stats["Transaction rows"] += 1

    def _parse_ledgers(self, text: str, fname: str, report: DailyReport) -> None:
        ledger = ""
        for n, line in iter_lines(text):
            c = collapse(line)
            if c in LEDGER_HEADERS:
                ledger = c
                continue
            label, nums = split_row(line)
            if not nums or len(nums) < 3:
                continue
            if label.lower().startswith("grand total"):
                report.stats["Ledgers Grand Total Difference"] = nums[2]
                continue
            if label.lower().startswith("overall total") and ledger:
                report.add(f"{ledger} Net Change", nums[2], "ledger", f"{self.pms}:{fname}:L{n}")
                report.stats[f"{ledger} Closing"] = nums[1]
                ledger = ""
            elif ledger:
                report.stats[f"{ledger} {label}"] = nums[1]
