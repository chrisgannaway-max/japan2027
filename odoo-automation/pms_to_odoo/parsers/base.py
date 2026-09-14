"""Parser interface plus shared text/number helpers."""
from __future__ import annotations

import csv
import re
from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional

from ..models import DailyReport, ReportLine

# "1,234.56"  "(1,234.56)"  "-1,234.56"  "$1,234.56"  "1,234.56-"  "1234.56CR"
_AMOUNT_RE = re.compile(
    r"^\s*(?P<neg1>[-(])?\s*\$?\s*(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
    r"\s*(?P<neg2>[-)]|CR)?\s*$", re.IGNORECASE)


def parse_amount(text: str) -> Optional[Decimal]:
    """Parse the number formats seen on PMS reports; None if the text is not a number."""
    m = _AMOUNT_RE.match(text or "")
    if not m:
        return None
    try:
        val = Decimal(m.group("num").replace(",", ""))
    except InvalidOperation:
        return None
    if m.group("neg1") or m.group("neg2"):
        val = -val
    return val


_DATE_PATTERNS = [
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), "%m/%d/%Y"),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2})\b"), "%m/%d/%y"),
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "%Y-%m-%d"),
    (re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})"), "%d-%b-%Y"),
    (re.compile(r"([A-Za-z]{3,9}) (\d{1,2}), (\d{4})"), "%B %d, %Y"),
]


def find_date(text: str, label_hint: str = "business date|audit date|date") -> Optional[date]:
    """Find the business date in report text, preferring one near a label hint."""
    hint = re.compile(rf"(?:{label_hint})[^\n]{{0,40}}", re.IGNORECASE)
    candidates: list[str] = [m.group(0) for m in hint.finditer(text)] + [text]
    for chunk in candidates:
        for pat, fmt in _DATE_PATTERNS:
            m = pat.search(chunk)
            if m:
                raw = m.group(0)
                for f in (fmt, "%b %d, %Y"):
                    try:
                        return datetime.strptime(raw, f).date()
                    except ValueError:
                        continue
    return None


def read_text(path: str | Path) -> str:
    """Return plain text for PDF / CSV / XLSX / TXT input so label+amount scanning can run."""
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".pdf":
        from pypdf import PdfReader  # pure Python, no system deps
        reader = PdfReader(str(p))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if suf in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
        rows = []
        for ws in wb.worksheets:
            rows.append(f"## sheet {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(cells):
                    rows.append("\t".join(cells))
        return "\n".join(rows)
    if suf == ".csv":
        with p.open(newline="", encoding="utf-8-sig") as fh:
            return "\n".join("\t".join(r) for r in csv.reader(fh))
    return p.read_text(encoding="utf-8", errors="replace")


class BaseParser(ABC):
    """Every PMS parser turns one report file into a DailyReport."""

    #: short identifier used in journal refs, e.g. "PEP"
    pms: str = "GENERIC"
    #: brand this PMS belongs to (documentation only)
    brand: str = ""
    #: file extensions this parser accepts
    extensions: tuple[str, ...] = (".pdf", ".csv", ".xlsx", ".txt")

    def accepts(self, path: str | Path) -> bool:
        return Path(path).suffix.lower() in self.extensions

    @abstractmethod
    def parse(self, path: str | Path, property_code: str,
              business_date: Optional[date] = None) -> DailyReport: ...

    # -- helpers shared by concrete parsers ------------------------------------
    @staticmethod
    def lines_from_label_amount_pairs(pairs: Iterable[tuple[str, Decimal, str]],
                                      source: str) -> list[ReportLine]:
        return [ReportLine(label=lbl.strip(), amount=amt, section=sec, source=source)
                for lbl, amt, sec in pairs]
