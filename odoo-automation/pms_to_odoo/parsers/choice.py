"""Choice Hotels - choiceADVANTAGE night-audit pack.

Sample: "Choice Hotels - Comfort Inn.pdf" (16 pages: Account Balance, A/R Aging, Departure
List, Final Transaction Closeout, Hotel Journal Summary, Hotel Statistics, In House List,
Ledger Summary, No Show, Pre-Paid, Shift Reconciliation, Tax Exempt).

We use:
* **Final Transaction Closeout** - gives each transaction code its type
  ("Transaction Type: ROOM REVENUE", "TAX", "CASH", "CREDIT CARDS", "ACCOUNTS RECEIVABLE"...).
* **Hotel Journal Summary** - one row per code with activity today:
      Description (CODE)  Postings  Corrections  Adjustments  Totals  Guest Ledger  AR Ledger  AdvDep Ledger  ...
  "Totals" is today's net; the three ledger columns on the "Today's Total:" row are the
  ledger movements.  Charges are positive, payments negative on this report.
* **Hotel Statistics** - occupancy / ADR / RevPAR for the stats block.

Internal moves between ledgers (Direct Bill DB/DR, Applied Credit AC / Credit Invoice CI)
are tagged ``transfer`` and not booked; their effect is already in the ledger columns.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..models import DailyReport
from .base import BaseParser, collapse, iter_lines, parse_date, read_text, split_row, split_stat_row

CODE_RE = re.compile(r"^(?P<label>.*?)\s*\((?P<code>[A-Z0-9]{1,8})\)\s*$")
PAYMENT_LABEL = re.compile(r"visa|master|amex|american express|discover|diners|jcb|\bcash\b|check|refund|"
                           r"franchise credit|direct pay|gift card|voucher", re.IGNORECASE)
STAT_LABELS = ("Total Rooms", "Rooms Available to Sell", "Total Occupied Rooms", "Total Revenue Rooms",
               "Comp Rooms", "Occ% of Total Available Rooms", "Occ% of Total Rooms",
               "ADR for Total Rev Rooms.", "RevPar", "Total Room Revenue", "Total Revenue",
               "Number of Adults", "Total Number of Guests", "Walk Ins", "No Shows", "Checked Out Today")


def section_for(code: str, label: str, tx_type: str) -> str:
    t = tx_type.upper()
    if "RECEIVABLE" in t or code in ("AC", "CI", "DB", "DR"):
        return "transfer"
    if t == "TAX" or re.search(r"\btax\b", label, re.I):
        return "tax"
    if t in ("CASH", "CREDIT CARDS", "ADMINISTRATION") or PAYMENT_LABEL.search(label):
        return "settlement"
    return "revenue"


class ChoiceAdvantageParser(BaseParser):
    pms = "CHOICEADV"
    brand = "Choice"
    expects = "choiceADVANTAGE night-audit pack containing 'Final Transaction Closeout' and 'Hotel Journal Summary'"

    def parse(self, path: str | Path, property_code: str,
              business_date: Optional[date] = None) -> DailyReport:
        text = read_text(path)
        flat = collapse(text)
        pid = re.search(r"Property Code:\s*([A-Z0-9]+)", flat)
        pname = re.search(r"Property Name:\s*(.+?)(?:\s+(?:Date Range|Business Date|Account|Room|Description)\b|$)", flat)
        d = None
        m = re.search(r"Hotel Journal Summary.*?Date Range:\s*(\d{1,2}/\d{1,2}/\d{4})", flat)
        if m:
            d = parse_date(m.group(1))
        if d is None:
            m = re.search(r"Business Date:\s*(\d{1,2}/\d{1,2}/\d{4})", flat)
            d = parse_date(m.group(1)) if m else None
        report = DailyReport(property_code=property_code, pms=self.pms,
                             business_date=business_date or d or date.today(), source_file=str(path),
                             pms_property_id=pid.group(1) if pid else "",
                             property_name=collapse(pname.group(1)) if pname else "")
        fname = Path(path).name

        # ---- pass 1: transaction code -> type, from Final Transaction Closeout ---------------
        code_type: dict[str, str] = {}
        cur_type = ""
        in_closeout = False
        for _, line in iter_lines(text):
            c = collapse(line)
            if c.lower().startswith("final transaction closeout"):
                in_closeout = True
            elif c.lower().startswith("date/time of printing") and in_closeout:
                in_closeout = False
            if not in_closeout:
                continue
            if c.startswith("Transaction Type:"):
                cur_type = c.split(":", 1)[1].strip()
                continue
            label, _ = split_row(line)
            cm = CODE_RE.match(label)
            if cm and cur_type:
                code_type[cm.group("code")] = cur_type
            else:
                # wrapped label: "(VDPEFT)" alone on the numbers line
                cm2 = re.match(r"^\((?P<code>[A-Z0-9]{1,8})\)$", label)
                if cm2 and cur_type:
                    code_type[cm2.group("code")] = cur_type

        # ---- pass 2: Hotel Journal Summary rows ------------------------------------------
        in_summary = False
        found_summary = False
        for n, line in iter_lines(text):
            c = collapse(line)
            lc = c.lower()
            if lc.startswith("hotel journal summary"):
                in_summary, found_summary = True, True
                continue
            if in_summary and lc.startswith("date/time of printing"):
                in_summary = False
                continue
            if not in_summary:
                continue
            label, nums = split_row(line)
            src = f"{self.pms}:{fname}:L{n}"
            if lc.startswith("today's total") and len(nums) >= 7:
                report.stats["Journal Total (today)"] = nums[3]
                report.add("Guest Ledger Net Change", nums[4], "ledger", src)
                report.add("AR Ledger Net Change", nums[5], "ledger", src)
                report.add("Advance Deposit Ledger Net Change", nums[6], "ledger", src)
                continue
            cm = CODE_RE.match(label)
            if not cm or len(nums) < 7:
                continue
            code, desc = cm.group("code"), collapse(cm.group("label"))
            section = section_for(code, desc, code_type.get(code, ""))
            total = nums[3]
            amount = -total if section == "settlement" else total
            report.add(desc, amount, section, src, code=code)
        if not found_summary:
            report.warnings.append("'Hotel Journal Summary' page not found: no ledger movements captured; "
                                   "make sure the full night-audit pack is exported.")
            report.recognised = False

        # ---- statistics --------------------------------------------------------------
        in_stats = False
        for _, line in iter_lines(text):
            c = collapse(line)
            if c.lower().startswith("hotel statistics"):
                in_stats = True
            elif c.lower().startswith("date/time of printing") and in_stats:
                in_stats = False
            if not in_stats:
                continue
            label, nums = split_stat_row(line)
            if label in STAT_LABELS and nums:
                report.stats[label] = nums[0]

        # control: sum of booked lines should equal journal total
        jt = report.stats.get("Journal Total (today)")
        if jt is not None:
            booked = report.total("revenue") + report.total("tax") - report.total("settlement") + report.total("transfer")
            if abs(booked - jt) > Decimal("0.01"):
                report.warnings.append(f"Booked lines ({booked}) differ from journal total ({jt}).")
        return report
