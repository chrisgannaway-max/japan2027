"""IHG - HotelKey "Trial Balance Report".

Sample: "OKCMD Trial Balance 9.30.25.pdf" (emailed as an attachment).

Rows:  TYPE  Account Name  Code  Opening  Debit  Credit  Net Change  Closing
with TYPE in ASSET / LIABILITY / INCOME / EXPENSE.  Long account names wrap onto extra
lines that are vertically centred on the row: the same number of fragments above and
below the line carrying the numbers.  We rebuild names with that rule.

HotelKey's trial balance is folio-centric: charges are debits and payments are credits,
and the "(Offset)" asset accounts are the ledger balances.  We normalise to the shared
convention (see models.py):

    INCOME     -> revenue     amount = net change
    LIABILITY  -> tax         amount = net change            (taxes collected)
    ASSET      -> settlement  amount = -net change           (payments received positive)
    ASSET *(Offset)* -> ledger  amount = -net change         (ledger increase positive)
    EXPENSE    -> expense     amount = net change            (guest paid-outs)

The report is self-balancing (Totals debit == credit), so the resulting entry balances.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..models import DailyReport
from .base import BaseParser, collapse, iter_lines, parse_date, read_text, split_row

TYPES = ("ASSET", "LIABILITY", "INCOME", "EXPENSE", "EQUITY")
HEADER_WORDS = {"transaction", "opening", "closing", "type", "account", "name", "debit", "credit",
                "net", "change", "code", "balance", "trial", "report"}
CODE_RE = re.compile(r"^[A-Z0-9_]{1,8}$")


def _join(fragments: list[str]) -> str:
    out = ""
    for f in fragments:
        if not f:
            continue
        if out and (out.endswith("(") or (re.search(r"[a-z]$", out) and re.match(r"^[a-z()]", f))):
            out += f            # word split mid-token: "reservation_in_h" + "ouse(Offset)"
        else:
            out = (out + " " + f).strip()
    return out


class IHGHotelKeyParser(BaseParser):
    pms = "HOTELKEY"
    brand = "IHG"
    expects = "HotelKey 'Trial Balance Report' (PDF or the .eml it is attached to)"

    def parse(self, path: str | Path, property_code: str,
              business_date: Optional[date] = None) -> DailyReport:
        text = read_text(path)
        flat = collapse(re.sub(r"===== (PAGE|ATTACHMENT)[^\n]*", " ", text))
        dr = re.search(r"Date Range:\s*([A-Za-z]{3} \d{1,2},? \d{4})\s*-\s*([A-Za-z]{3} \d{1,2},? \d{4})", flat)
        d = parse_date(dr.group(1)) if dr else None
        report = DailyReport(property_code=property_code, pms=self.pms,
                             business_date=business_date or d or date.today(), source_file=str(path))
        if dr and dr.group(1) != dr.group(2):
            report.warnings.append(f"Report covers a date range ({dr.group(1)} - {dr.group(2)}), not a single day.")
        if "Trial Balance Report" not in flat:
            report.warnings.append("This does not look like a HotelKey Trial Balance Report.")
            report.recognised = False

        # header block: hotel name lines then a bare property id like "OKCMD"
        head = [collapse(l) for _, l in list(iter_lines(text))[:6]]
        for h in head:
            first = h.split(" ")[0]
            if re.fullmatch(r"[A-Z0-9]{3,8}", first) and not h.lower().startswith(("date", "report", "user", "trial")):
                report.pms_property_id = first
                break
        nm = re.match(r"(.+?)\s+Date Range:", flat)
        report.property_name = re.sub(r"\s+[A-Z0-9]{3,8}$", "", nm.group(1)).strip() if nm else ""

        rows: list[dict] = []
        frags: list[str] = []
        fname = Path(path).name
        for n, line in iter_lines(text):
            c = collapse(line)
            words = set(re.findall(r"[a-z]+", c.lower()))
            if len(words) >= 2 and words <= HEADER_WORDS and not c.upper().startswith(TYPES):
                frags = []                                 # column header lines (start of page)
                continue
            if re.search(r"date range:|report run|user:", c.lower()):
                frags = []                                 # report header block
                continue
            label, nums = split_row(line)
            lc = label.lower()
            if lc.startswith("sub total") or lc.startswith("totals"):
                if lc.startswith("totals") and len(nums) >= 2:
                    report.stats["Trial Balance Debit"], report.stats["Trial Balance Credit"] = nums[0], nums[1]
                frags = []
                continue
            if len(nums) >= 4:
                toks = label.split(" ") if label else []
                typ = toks[0] if toks and toks[0] in TYPES else ""
                rest = toks[1:] if typ else toks
                code = ""
                if rest and CODE_RE.match(rest[-1]) and (len(rest) > 1 or frags):
                    code, rest = rest[-1], rest[:-1]
                rows.append({"type": typ, "mid": " ".join(rest), "code": code, "nums": nums,
                             "above": frags, "below": [], "need": len(frags), "src": f"{self.pms}:{fname}:L{n}"})
                frags = []
            elif label and not nums:
                if rows and rows[-1]["need"] > 0:
                    rows[-1]["below"].append(label)
                    rows[-1]["need"] -= 1
                else:
                    frags.append(label)

        for r in rows:
            name = _join(r["above"] + [r["mid"]] + r["below"]) or r["code"]
            net = r["nums"][3] if len(r["nums"]) >= 5 else r["nums"][-1]
            typ = r["type"]
            if typ == "ASSET":
                section = "ledger" if re.search(r"\(\s*offset\s*\)", name, re.I) else "settlement"
                amount = -net
            elif typ == "LIABILITY":
                section, amount = "tax", net
            elif typ == "INCOME":
                section, amount = "revenue", net
            elif typ == "EXPENSE":
                section, amount = "expense", net
            else:
                section, amount = "", net
            report.add(name, amount, section, r["src"], code=r["code"])
        if not rows:
            report.warnings.append("No trial balance rows recognised.")
        return report
