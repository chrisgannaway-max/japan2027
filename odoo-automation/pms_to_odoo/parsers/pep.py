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

Hilton Garden Inn prints the same report differently, and both come through here: the section
name stands on its own line above the column header rather than sharing it, the Room Revenue
tables carry an extra "Adjusted Transferred" column that the Charges table does not, ledgers
come as one "Ledger Name / Opening / Net Change / Closing" table instead of three paragraphs,
and "Revenue & Charges" is a recap of tables already printed rather than the heading above them.

We book the **Net Today** column (Actual + Adjusted), read by where it is printed rather than
by counting columns, since the column count changes between tables of the one report.  Labels
that wrap over two lines are printed as  text / numbers / text  and are re-joined.

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
from .base import (BaseParser, MONEY_TOKEN, collapse, iter_lines, parse_amount, parse_date,
                   read_text, split_row, split_stat_row)

#: Headings that stand alone on their own line.  Hilton Garden Inn opens each table this way
#: ("Room Revenue", then the column header beneath); Embassy Suites puts the name on the same
#: line as the columns.  Both layouts are the same report.
BARE_SECTIONS = {
    "room revenue": "revenue",
    "other room revenue": "revenue",
    "charges": "revenue",
    "taxes": "tax",
    "payments": "settlement",
    "balance information": "ledger",
}

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
    "statistical counts": "stats",
    "turn away information": "skip",
    "cash deposit and cash drop": "skip",     # cash control, not a posting
    # Both segment tables re-cut the night by market segment: the same money again.
    "statistics by market segment": "skip",
    "revenue by market segment": "skip",
}


_MONEY = re.compile(MONEY_TOKEN)


def _amount_under(line: str, net_end: Optional[int]) -> Optional[Decimal]:
    """The figure printed under the "Net Today" heading.

    Counting columns does not survive this report: one Hilton adds an "Adjusted Transferred"
    column whose heading wraps onto its own line, and it appears in some tables and not others --
    within the same document.  The printed position is the reliable thing, and these are
    right-aligned columns, so the right-hand edges line up to within a character or two.
    """
    if net_end is None:
        return None
    best = None
    for m in _MONEY.finditer(line):
        gap = abs(m.end() - net_end)
        if gap <= 6 and (best is None or gap < best[0]):
            best = (gap, parse_amount(m.group()))
    return best[1] if best else None


def _is_heading(rows: list, idx: int) -> bool:
    """Is the bare name on this line opening a table, or is it the tail of a wrapped label?

    A heading is followed by the table's column header within a line or two.  A charge code
    that happens to read like a section name -- "LATE CANCEL" / "ROOM  REVENUE" wrapped over
    two lines -- is followed by the next row's figures instead.
    """
    for _, nxt in rows[idx + 1: idx + 4]:
        low = nxt.lower()
        if ("actual today" in low and "net today" in low) or "ledger name" in low:
            return True
        if _MONEY.search(nxt):
            return False
    return False


def _bare_code(text: str) -> str:
    """The property code when it is printed on its own line rather than after "Hotel ID :".

    Hilton Garden Inn puts the hotel name on one line and OKCAH on the next; Embassy Suites
    labels it.  Only the first few lines are considered, because further down the report is full
    of upper-case words -- REVENUE, PREPAID, ALLOWANCE -- that would match just as well.
    """
    for raw in text.splitlines()[:12]:
        line = collapse(raw)
        # Either the code alone on its line, or first on a line it shares with the run details --
        # "OKCAH  Report run date: Nov 12, 2025" is how Hilton Garden Inn prints it.
        m = re.match(r"([A-Z][A-Z0-9]{2,9})(?:\s+Report\s+run|\s*$)", line)
        if m and m.group(1) not in ("PAGE", "USD", "FINAL", "DATE"):
            return m.group(1)
    return ""


class HiltonPEPParser(BaseParser):
    pms = "PEP"
    brand = "Hilton"
    expects = "PEP 'Final Audit' report (FinalAudit PDF)"

    def parse(self, path: str | Path, property_code: str,
              business_date: Optional[date] = None) -> DailyReport:
        text = read_text(path)
        flat = collapse(re.sub(r"===== (PAGE|ATTACHMENT)[^\n]*", " ", text))
        m = re.search(r"Hotel ID\s*:\s*([A-Z0-9]+)", flat)
        hotel_id = m.group(1) if m else _bare_code(text)
        d = None
        for dm in re.finditer(r"(?<!Run )\bDate\s*:\s*([A-Za-z]{3} \d{1,2}, \d{4})", flat):
            d = parse_date(dm.group(1))
            break
        report = DailyReport(property_code=property_code, pms=self.pms,
                             business_date=business_date or d or date.today(),
                             source_file=str(path), pms_property_id=hotel_id)
        nm = re.match(r"(.+?)\s+Date\s*:", flat)
        report.property_name = nm.group(1).strip() if nm else ""
        if "Final Audit" not in flat:
            report.warnings.append("This does not look like a PEP Final Audit report "
                                   "(expected the text 'Final Audit').")
            report.recognised = False

        section = ""
        col = 2                      # index of "Net Today", when position cannot be used
        net_end: Optional[int] = None    # where "Net Today" ends on the current header line
        ledger_table = False             # the "Ledger Name / Opening / Net Change / Closing" form
        sub = ""                         # a sub-table booked differently from its section
        pending_label: Optional[str] = None
        expecting_tail = None        # ReportLine whose label continues on the next line
        fname = Path(path).name
        rows = list(iter_lines(text))
        for idx, (n, line) in enumerate(rows):
            c = collapse(line)
            lc = c.lower()
            if re.fullmatch(r"page \d+ of \d+", lc) or lc.startswith("reviewed by"):
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
                nt = re.search(r"Net\s+Today", line)
                net_end = nt.end() if nt else None
                ledger_table = False
                # A payment settled by billing a company is not money taken in: it moves the
                # balance to the Direct Bill ledger, which the ledger table already reports.
                # It is a sub-table of Payments, so the override lasts until the next
                # sub-table -- the section itself is still Payments, and its grand total
                # is printed at the foot of this table.
                sub = "transfer" if title == "direct bill" else ""
                pending_label, expecting_tail = None, None
                continue

            hdr = next((sec for key, sec in SECTION_HEADERS.items()
                        if lc == key or lc.startswith(key + " ")), None)
            if hdr is None and lc in BARE_SECTIONS and _is_heading(rows, idx):
                hdr = BARE_SECTIONS[lc]
            if hdr is not None:
                section = hdr
                net_end, ledger_table, sub = None, False, ""
                pending_label, expecting_tail = None, None
                continue

            if section == "ledger" and lc.startswith("ledger name") and "net change" in lc:
                ledger_table = True
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
                    amount = _amount_under(line, net_end)
                    if amount is None:
                        amount = nums[min(col, len(nums) - 1)]
                    if label.lower().startswith("total"):
                        report.stats[f"{label} (Net Today)"] = amount
                        expecting_tail = None
                        continue
                    rl = report.add(label, amount, sub or section, src)
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
                if ledger_table:
                    # Opening | Net Change | Closing.  The middle column is the night's movement.
                    if len(nums) >= 3 and not label.lower().startswith("total"):
                        report.add(f"{label} Net Change", nums[1], "ledger", src)
                    elif label.lower().startswith("total") and len(nums) >= 3:
                        report.stats["Ledger Totals Net Change"] = nums[1]
                    continue
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

        # "Revenue & Charges" recaps the tables above it, line for line -- and one Hilton prints
        # that recap before the Taxes table it names, with labels that wrap over two lines. So
        # the recap can only be recognised at the end, once every label is whole and every
        # section has been seen: any booked line whose label is the name of a section is a
        # summary of that section, not a charge. Booking them counts everything twice, in
        # balance, which is the worst way to be wrong.
        recap = {k for k, v in BARE_SECTIONS.items() if v in ("revenue", "tax", "settlement")}
        recap |= {k for k, v in SECTION_HEADERS.items() if v in ("revenue", "tax", "settlement")}
        kept = []
        for l in report.lines:
            if l.section in ("revenue", "tax", "settlement") and l.label.strip().lower() in recap:
                report.stats[f"{l.label} (section recap)"] = l.amount
            else:
                kept.append(l)
        report.lines = kept

        b, e = report.stats.get("Hotel Balance Beginning Balance"), report.stats.get("Hotel Balance Ending Balance")
        if b is not None and e is not None:
            report.stats["Hotel Balance Net Change"] = e - b
            ledger_sum = report.total("ledger")
            if abs(ledger_sum - (e - b)) > Decimal("0.01"):
                report.warnings.append(
                    f"Ledger net changes ({ledger_sum}) differ from Hotel Balance change ({e - b}).")
        return report
