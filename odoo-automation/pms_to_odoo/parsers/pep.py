"""Hilton - PEP "Final Audit" report.

Sample: "Nov 11, 2025-OKCON-Embassy Suites by Hilton ...-FinalAuditV2.pdf" (8 pages).

Layout (pdfplumber layout text):
    Revenue & Charges            <- section; sub-tables "Revenue" and "Charges"
      <label>  Actual Today  Adjusted  Net Today  M-T-D  LY-M-T-D  Variance  Y-T-D  LY-T-D  Variance
    Taxes                        <- same 9 columns
    Payment Information          <- sub-tables Cash / Card / Other / DIRECT BILL
    Deposit Information, Cash Drop Information   (cash control - skipped)
    Guest Ledger Balance / Direct Bill Balance / Advanced Deposit Balance
      "<ledger> Net Change: $x"  <- ledger movements
    Hotel Balance  Beginning/Ending
    Room Statistics / Performance Statistics

We book the **Net Today** column (Actual + Adjusted).  Labels that wrap over two lines
are printed as  text / numbers / text  and are re-joined.

Balance check built into the report: Total Revenues + Total Taxes - Total Payments
== Hotel Balance Ending - Beginning == sum of the ledger "Net Change" lines.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..models import DailyReport
from .base import BaseParser, collapse, parse_date, read_text, split_row, split_stat_row, iter_lines

SECTION_HEADERS = {
    "revenue & charges": "revenue",
    "payment information": "settlement",
    "deposit information": "skip",
    "cash drop information": "skip",
    "guest ledger balance": "ledger",
    "direct bill balance": "ledger",
    "advanced deposit balance": "ledger",
    "advance deposit balance": "ledger",
    "hotel balance": "hotel",
    "room statistics": "stats",
    "performance statistics": "stats",
    "turn away information": "skip",
}


class HiltonPEPParser(BaseParser):
    pms = "PEP"
    brand = "Hilton"
    expects = "PEP 'Final Audit' report (FinalAudit PDF)"

    def parse(self, path: str | Path, property_code: str,
              business_date: Optional[date] = None) -> DailyReport:
        text = read_text(path)
        flat = collapse(re.sub(r"===== (PAGE|ATTACHMENT)[^\n]*", " ", text))
        m = re.search(r"Hotel ID\s*:\s*([A-Z0-9]+)", flat)
        d = None
        for dm in re.finditer(r"(?<!Run )\bDate\s*:\s*([A-Za-z]{3} \d{1,2}, \d{4})", flat):
            d = parse_date(dm.group(1))
            break
        report = DailyReport(property_code=property_code, pms=self.pms,
                             business_date=business_date or d or date.today(),
                             source_file=str(path), pms_property_id=m.group(1) if m else "")
        nm = re.match(r"(.+?)\s+Date\s*:", flat)
        report.property_name = nm.group(1).strip() if nm else ""
        if "Final Audit" not in flat:
            report.warnings.append("This does not look like a PEP Final Audit report "
                                   "(expected the text 'Final Audit').")
            report.recognised = False

        section = ""
        col = 2                      # index of "Net Today" among the numeric columns
        pending_label: Optional[str] = None
        expecting_tail = None        # ReportLine whose label continues on the next line
        fname = Path(path).name
        for n, line in iter_lines(text):
            c = collapse(line)
            lc = c.lower()
            if re.fullmatch(r"page \d+ of \d+", lc) or lc.startswith("reviewed by"):
                continue
            hdr = next((sec for key, sec in SECTION_HEADERS.items() if lc == key or lc.startswith(key + " ")), None)
            if hdr is not None:
                section = hdr
                pending_label, expecting_tail = None, None
                continue
            if "actual today" in lc and "net today" in lc:
                cols = [collapse(x) for x in re.split(r"\s{2,}", line.strip())]
                title = cols[0].lower() if cols else ""
                if title == "taxes":
                    section = "tax"
                elif title in SECTION_HEADERS:          # e.g. "Deposit Information   Actual Today ..."
                    section = SECTION_HEADERS[title]
                nums_hdr = [x for x in cols if x.lower() not in ("revenue", "charges", "cash", "card", "other",
                                                                  "direct bill", "taxes")]
                if "Net Today" in nums_hdr:
                    col = nums_hdr.index("Net Today")
                pending_label, expecting_tail = None, None
                continue

            src = f"{self.pms}:{fname}:L{n}"
            if section in ("revenue", "tax", "settlement"):
                label, nums = split_row(line)
                if nums and len(nums) >= 3:
                    tail = False
                    if not label and pending_label:
                        label, tail = pending_label, True
                    pending_label = None
                    if not label:
                        continue
                    amount = nums[min(col, len(nums) - 1)]
                    if label.lower().startswith("total"):
                        report.stats[f"{label} (Net Today)"] = amount
                        expecting_tail = None
                        continue
                    rl = report.add(label, amount, section, src)
                    expecting_tail = rl if tail else None
                elif not nums and label:
                    if expecting_tail is not None:
                        expecting_tail.label = f"{expecting_tail.label} {label}"
                        expecting_tail = None
                    else:
                        pending_label = label
            elif section == "ledger":
                label, nums = split_row(line)
                if not nums:
                    continue
                label = label.rstrip(": ")
                if label.lower().endswith("net change"):
                    report.add(label, nums[0], "ledger", src)
                else:
                    report.stats[label] = nums[0]
            elif section == "hotel":
                label, nums = split_row(line)
                if nums:
                    report.stats[f"Hotel Balance {label.rstrip(': ')}"] = nums[0]
            elif section == "stats":
                label, nums = split_stat_row(line)
                if label and nums and not label.lower().startswith("page "):
                    report.stats[label] = nums[0]

        b, e = report.stats.get("Hotel Balance Beginning Balance"), report.stats.get("Hotel Balance Ending Balance")
        if b is not None and e is not None:
            report.stats["Hotel Balance Net Change"] = e - b
            ledger_sum = report.total("ledger")
            if abs(ledger_sum - (e - b)) > Decimal("0.01"):
                report.warnings.append(
                    f"Ledger net changes ({ledger_sum}) differ from Hotel Balance change ({e - b}).")
        return report
