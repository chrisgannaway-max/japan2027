"""Marriott - Agilysys Stay "Ledger Summary" (Group By: Ledger).

Sample: "Ledger_Marriott_..._Summary_OKCAW_2026-07-09_10-47-11.pdf" (4 pages).

    CATEGORY : CITY | DEPOSIT | GUEST            <- one block per PMS ledger
      SUBCATEGORY : 2026-07-08                    <- business date; next numbers line =
        $beginning  $ending                          ledger BEGINNING and ENDING balance
      TRANSACTION TYPE : PAYMENTS | REVENUE | TRANSFERS
        $type total  $0.00  $0.00
        <item>  <code>  <gl code?>  $amount  $beginning  $ending

Signs on the report: charges positive, payments negative, transfers from the ledger's own
point of view.  "REVENUE" mixes revenue and tax items; tax items have codes starting with
T and "Tax" in the name.  Transfers between ledgers net to zero and are tagged
``transfer`` (not booked); the ledger balance changes carry them.

Control identity per ledger: beginning + revenue + payments + transfers == ending, and
across ledgers revenue + tax - payments == sum of ledger changes.

The report has a GL CODE column (empty on the sample).  If the property maintains GL
codes inside Agilysys, they are captured into ReportLine.code_gl for direct mapping.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..models import DailyReport
from .base import BaseParser, collapse, iter_lines, parse_amount, parse_date, read_text, split_row

LEDGER_NAMES = {"CITY": "City Ledger", "DEPOSIT": "Deposit Ledger", "GUEST": "Guest Ledger",
                "PACKAGE": "Package Ledger", "AR": "City Ledger"}
CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{0,4}$")


class MarriottAgilysysParser(BaseParser):
    pms = "AGILYSYS"
    brand = "Marriott"
    expects = "Agilysys Stay 'Ledger Summary' report grouped by ledger"

    CSV_HEADER = ("CATEGORY", "SUBCATEGORY", "TRANSACTION TYPE", "TRANSACTION ITEM", "TRANSACTION CODE",
                  "GL CODE", "AMOUNT", "BEGINNING", "ENDING")

    def parse(self, path: str | Path, property_code: str,
              business_date: Optional[date] = None) -> DailyReport:
        p = Path(path)
        if p.suffix.lower() == ".csv":
            return self._parse_csv(p, property_code, business_date)
        text = read_text(path)
        flat = collapse(re.sub(r"===== (PAGE|ATTACHMENT)[^\n]*", " ", text))
        sd = re.search(r"Start Date\s*:\s*([A-Za-z]{3} \d{1,2}, \d{4})", flat)
        ed = re.search(r"End Date\s*:\s*([A-Za-z]{3} \d{1,2}, \d{4})", flat)
        d = parse_date(sd.group(1)) if sd else None
        pid = re.search(r"Property Id\s*:\s*(.+?)\s*\|\|", flat)
        report = DailyReport(property_code=property_code, pms=self.pms,
                             business_date=business_date or d or date.today(), source_file=str(path),
                             property_name=collapse(pid.group(1)) if pid else "")
        if sd and ed and sd.group(1) != ed.group(1):
            report.warnings.append(f"Report covers {sd.group(1)} - {ed.group(1)}, not a single day.")
        if "ledger summary" not in flat.lower() or "agilysys" not in flat.lower():
            report.warnings.append("This does not look like an Agilysys Stay Ledger Summary.")
            report.recognised = False

        fname = Path(path).name
        category = ""          # CITY / DEPOSIT / GUEST
        tx_type = ""           # PAYMENTS / REVENUE / TRANSFERS
        pending = ""           # header whose totals line is expected next
        for n, line in iter_lines(text):
            c = collapse(line)
            lc = c.lower()
            src = f"{self.pms}:{fname}:L{n}"
            if re.fullmatch(r"\d+ / \d+", c) or lc.startswith(("agilysys", "property id", "type :")) \
                    or re.match(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", c) or "transaction item" in lc:
                continue
            m = re.match(r"CATEGORY\s*:\s*(\w+)", c, re.I)
            if m and not lc.startswith("subcategory"):
                category, tx_type, pending = m.group(1).upper(), "", "category"
                continue
            m = re.match(r"SUBCATEGORY\s*:\s*(\S+)", c, re.I)
            if m:
                pending = "subcategory"
                continue
            m = re.match(r"TRANSACTION\s+TYPE\s*:\s*(\w+)", c, re.I)
            if m:
                tx_type, pending = m.group(1).upper(), "type"
                continue
            label, nums = split_row(line)
            if not nums:
                continue
            ledger = LEDGER_NAMES.get(category, f"{category.title()} Ledger")
            if not label:                                  # totals line belonging to the last header
                if pending == "subcategory" and len(nums) >= 2:
                    beginning, ending = nums[0], nums[1]
                    report.stats[f"{ledger} Beginning"] = beginning
                    report.stats[f"{ledger} Ending"] = ending
                    report.add(f"{ledger} Net Change", ending - beginning, "ledger", src)
                elif pending == "type" and nums:
                    report.stats[f"{ledger} {tx_type.title()} Total"] = nums[0]
                pending = ""
                continue
            pending = ""
            toks = label.split(" ")
            code = ""
            if len(toks) > 1 and CODE_RE.match(toks[-1]):
                code, label = toks[-1], " ".join(toks[:-1])
            amount = nums[0]
            if tx_type == "PAYMENTS":
                report.add(label, -amount, "settlement", src, code=code)
            elif tx_type == "REVENUE":
                is_tax = bool(re.search(r"\btax\b", label, re.I)) or code.startswith("T")
                report.add(label, amount, "tax" if is_tax else "revenue", src, code=code)
            elif tx_type == "TRANSFERS":
                report.add(f"{ledger}: {label}", amount, "transfer", src, code=code)
            else:
                report.add(label, amount, "", src, code=code)

        if abs(report.total("transfer")) > Decimal("0.01"):
            report.warnings.append(f"Inter-ledger transfers do not net to zero ({report.total('transfer')}).")
        if not report.lines:
            report.warnings.append("No ledger rows recognised.")
        return report

    # ------------------------------------------------------------------ CSV export
    def _parse_csv(self, p: Path, property_code: str, business_date: Optional[date]) -> DailyReport:
        """Agilysys "Ledger Summary" exported as CSV (what the properties call the Excel).

        Columns: CATEGORY, SUBCATEGORY (business date), TRANSACTION TYPE, TRANSACTION ITEM,
        TRANSACTION CODE, GL CODE, AMOUNT, BEGINNING, ENDING.  Rows with a blank TRANSACTION
        TYPE are the ledger balance rows (BEGINNING / ENDING); rows with a type but no item
        are type subtotals.  Some exports omit the subtotal rows, in which case the ledger
        movements are missing and the report says so.
        """
        raw = p.read_text(encoding="utf-8-sig", errors="replace").lstrip("\ufeff")   # some exports carry two BOMs
        rows = [{(k or "").strip().lstrip("\ufeff").upper(): v for k, v in r.items()} for r in csv.DictReader(io.StringIO(raw))]
        header = tuple(rows[0].keys()) if rows else ()
        report = DailyReport(property_code=property_code, pms=self.pms,
                             business_date=business_date or date.today(), source_file=str(p))
        if not rows or not set(self.CSV_HEADER[:4]) <= set(header):
            report.warnings.append("This CSV is not an Agilysys Ledger Summary export (unexpected columns).")
            report.recognised = False
            return report
        m = re.search(r"([A-Za-z0-9]+)_\d{4}-\d{2}-\d{2}", p.stem)   # Ledger_Summary_OKCAW_2026-07-09_...
        report.pms_property_id = m.group(1) if m else ""
        get = lambda r, k: (r.get(k) or "").strip()  # noqa: E731
        dates = set()
        have_balances = False
        for i, r in enumerate(rows, start=2):
            cat, sub, typ = get(r, "CATEGORY").upper(), get(r, "SUBCATEGORY"), get(r, "TRANSACTION TYPE").upper()
            item, code, gl = get(r, "TRANSACTION ITEM"), get(r, "TRANSACTION CODE"), get(r, "GL CODE")
            amount = parse_amount(get(r, "AMOUNT")) or Decimal("0")
            d = parse_date(sub)
            if d:
                dates.add(d)
            ledger = LEDGER_NAMES.get(cat, f"{cat.title()} Ledger")
            src = f"{self.pms}:{p.name}:R{i}"
            if not typ:                                      # ledger balance row
                b, e = parse_amount(get(r, "BEGINNING")), parse_amount(get(r, "ENDING"))
                if b is not None and e is not None:
                    have_balances = True
                    report.stats[f"{ledger} Beginning"], report.stats[f"{ledger} Ending"] = b, e
                    report.add(f"{ledger} Net Change", e - b, "ledger", src)
                continue
            if not item:                                     # type subtotal row
                report.stats[f"{ledger} {typ.title()} Total"] = amount
                continue
            if typ == "PAYMENTS":
                line = report.add(item, -amount, "settlement", src, code=code)
            elif typ == "REVENUE":
                is_tax = bool(re.search(r"\btax\b", item, re.I)) or code.startswith("T")
                line = report.add(item, amount, "tax" if is_tax else "revenue", src, code=code)
            elif typ == "TRANSFERS":
                line = report.add(f"{ledger}: {item}", amount, "transfer", src, code=code)
            else:
                line = report.add(item, amount, "", src, code=code)
            if gl:
                line.gl_code = gl
        if business_date is None and len(dates) == 1:
            report.business_date = dates.pop()
        elif len(dates) > 1:
            report.warnings.append(f"CSV covers several dates: {sorted(dates)}")
        if not have_balances:
            report.warnings.append("Export has no ledger balance rows (BEGINNING/ENDING): ledger movements "
                                   "missing, the entry will not balance. Export the summary with subtotals.")
        if abs(report.total("transfer")) > Decimal("0.01"):
            report.warnings.append(f"Inter-ledger transfers do not net to zero ({report.total('transfer')}).")
        return report
