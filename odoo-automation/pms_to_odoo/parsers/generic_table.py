"""Config-driven parser that works for any 'label ....... amount' style report.

Most night-audit summaries (PEP Daily Revenue Report, Opera Trial Balance / Manager
Report, HotelKey Daily Summary, Choice Advantage Daily Report, Agilysys Daily Revenue,
Synxis/Opera Cloud journals) are ultimately a list of labels with one or more numeric
columns (Today / MTD / YTD).  This parser:

1. converts the file to text (PDF, CSV, XLSX, TXT),
2. tracks the current *section* using configurable section headers,
3. captures ``label  amount [amount ...]`` rows and keeps the column you choose
   (default: the first numeric column = "Today"),
4. optionally applies a line-item allow list, so rows like "Occupancy %" are treated
   as statistics rather than money.

Everything is driven by a small ``layout`` dict so each PMS subclass only overrides
what differs.  Once we receive the real sample reports the layouts get filled in and,
where a report is genuinely tabular (CSV/XLSX exports), a subclass can override
``parse`` entirely.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..models import DailyReport, ReportLine
from .base import BaseParser, find_date, parse_amount, read_text

DEFAULT_LAYOUT: dict = {
    # section header regexes -> section name.  Checked against whole lines.
    "sections": {
        r"^\s*(room|rooms|f&b|food|beverage|other|total)?\s*revenue": "revenue",
        r"^\s*tax(es)?\b": "tax",
        r"^\s*(settlement|payments?|receipts?|cash\s*(and|&)\s*credit|credit\s*cards?)": "settlement",
        r"^\s*(ledger|guest ledger|city ledger|a/?r|advance deposits?|deposit ledger)": "ledger",
        r"^\s*(statistics|stats|occupancy|room statistics)": "statistics",
    },
    # regex with named groups 'label' and 'amounts' (the numeric tail of the row)
    "row": r"^(?P<label>[A-Za-z][A-Za-z0-9 &/%'().,\-_]*?)\s*[:.]*\s+(?P<amounts>(?:[-(]?\$?[\d,]+(?:\.\d{1,2})?[-)]?(?:\s*CR)?\s*)+)$",
    # which numeric column to keep (0 = first column, usually "Today")
    "amount_column": 0,
    # labels matched by these regexes are stored in report.stats instead of lines
    # NOTE: anchored to the WHOLE label so "No-Show Revenue" / "County Occupancy Tax"
    # stay financial lines while "No Shows" / "Occupancy %" become statistics.
    "stats": [r"^occupancy( ?%| rate)?$", r"^adr$", r"^revpar$", r"^average (daily )?rate$",
              r"^rooms? (sold|occupied|available|out of order|ooo|comp(limentary)?)$",
              r"^arrivals?$", r"^departures?$", r"^no.?shows?$", r"^stay.?overs?$",
              r"^house count$", r"^guests? in house$", r"^walk.?ins?$"],
    # lines matched by these regexes are dropped entirely (page footers, totals we recompute)
    "skip": [r"^page \d+", r"^printed", r"^run date", r"^grand total$", r"^total$"],
    "date_hint": r"business date|audit date|date",
}


class GenericTableParser(BaseParser):
    pms = "GENERIC"
    layout: dict = DEFAULT_LAYOUT

    def __init__(self, layout_overrides: Optional[dict] = None):
        merged = {**DEFAULT_LAYOUT, **(self.layout if self.layout is not DEFAULT_LAYOUT else {})}
        if layout_overrides:
            merged.update(layout_overrides)
        self.cfg = merged
        self._sections = [(re.compile(p, re.IGNORECASE), name) for p, name in self.cfg["sections"].items()]
        self._row = re.compile(self.cfg["row"])
        self._stats = [re.compile(p, re.IGNORECASE) for p in self.cfg["stats"]]
        self._skip = [re.compile(p, re.IGNORECASE) for p in self.cfg["skip"]]

    def parse(self, path: str | Path, property_code: str,
              business_date: Optional[date] = None) -> DailyReport:
        text = read_text(path)
        report = DailyReport(property_code=property_code, pms=self.pms,
                             business_date=business_date or find_date(text, self.cfg["date_hint"]) or date.today(),
                             source_file=str(path))
        section = ""
        for n, raw in enumerate(text.splitlines(), start=1):
            line = raw.replace("\t", "  ").rstrip()
            if not line.strip():
                continue
            if any(p.search(line.strip()) for p in self._skip):
                continue
            for pat, name in self._sections:
                if pat.search(line.strip()) and parse_amount(line.strip().split()[-1]) is None:
                    section = name
                    break
            m = self._row.match(line.strip())
            if not m:
                continue
            label = m.group("label").strip(" .:")
            nums = [parse_amount(tok) for tok in re.findall(r"[-(]?\$?[\d,]+(?:\.\d{1,2})?[-)]?(?:\s*CR)?", m.group("amounts"), re.I)]
            nums = [x for x in nums if x is not None]
            if not nums:
                continue
            col = min(self.cfg["amount_column"], len(nums) - 1)
            amount: Decimal = nums[col]
            if any(p.fullmatch(label.strip()) for p in self._stats):
                report.stats[label] = amount
                continue
            report.lines.append(ReportLine(label=label, amount=amount, section=section,
                                           source=f"{self.pms}:{Path(path).name}:L{n}"))
        return report
