"""Parser interface plus shared text/number helpers.

Text extraction
---------------
PDF night-audit packs are read with pdfplumber in *layout* mode, which keeps each
printed row on one text line with columns in visual order.  Plain text extraction
(pypdf) scrambles column order on several of these reports (PEP, Opera), so it is only
the fallback.  ``.eml`` files (the way HotelKey reports reach accounting) are opened and
their PDF attachments are extracted.
"""
from __future__ import annotations

import csv
import email
import re
import tempfile
from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from email import policy
from pathlib import Path
from typing import Iterable, Optional

from ..models import DailyReport, ReportLine

# Money tokens as printed by the PMSs we have seen:
#   1,234.56   $1,234.56   -$308.00   -USD5.61   USD0.00   (1,234.56)   - 64.06   123.45-   50.00CR
#   $(3.00)  ($3.00)  and never the "4.12" inside a rate like "4.125%"
MONEY_TOKEN = (r"(?<![\w.])(?:USD|\$)?\s?\(?\s?-?\s?(?:USD|\$)?\s?-?\d[\d,]*\.\d{2}(?![\d%])\)?(?:-|\s?CR)?")
MONEY_RE = re.compile(MONEY_TOKEN, re.IGNORECASE)
# statistics: integers and percentages ("216", "2,160", "99.53%", "42.86 %")
STAT_TOKEN = r"-?\d[\d,]*(?:\.\d+)?\s?%?"
STAT_RE = re.compile(STAT_TOKEN)


def collapse(text: str) -> str:
    """Collapse runs of whitespace (layout-mode text spaces words out)."""
    return re.sub(r"\s+", " ", text).strip()


def parse_amount(text: str) -> Optional[Decimal]:
    """Parse one money/number token; None if it is not numeric."""
    if text is None:
        return None
    t = text.strip().replace("USD", "").replace("$", "").replace(",", "").replace(" ", "").rstrip("%")
    if not t:
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t.upper().endswith("CR"):
        neg, t = True, t[:-2]
    if t.endswith("-"):
        neg, t = True, t[:-1]
    while t.startswith("-"):
        neg, t = (not neg), t[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)?", t):
        return None
    try:
        val = Decimal(t)
    except InvalidOperation:
        return None
    return -val if neg else val


def money_tokens(line: str) -> list[tuple[int, int, Decimal]]:
    """All money tokens in a line as (start, end, value)."""
    out = []
    for m in MONEY_RE.finditer(line):
        v = parse_amount(m.group(0))
        if v is not None:
            out.append((m.start(), m.end(), v))
    return out


def split_row(line: str) -> tuple[str, list[Decimal]]:
    """Split 'LABEL   $1.00   $2.00' into ('LABEL', [1.00, 2.00]).

    The label is everything before the first money token, whitespace-collapsed.
    """
    toks = money_tokens(line)
    if not toks:
        return collapse(line), []
    return collapse(line[: toks[0][0]]), [v for _, _, v in toks]


def split_stat_row(line: str) -> tuple[str, list[Decimal]]:
    """Like split_row but accepts integers and percentages (statistics tables)."""
    pad = " " + line
    m = re.search(r"\s(?:USD|\$)?(-?\d[\d,]*(?:\.\d+)?\s?%?)(?=\s|$)", pad)
    if not m:
        return collapse(line), []
    label = collapse(pad[: m.start(0)])
    vals = [parse_amount(t) for t in STAT_RE.findall(pad[m.start(0):])]
    return label, [v for v in vals if v is not None]


_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"), ("%m/%d/%Y",)),
    (re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2})\b"), ("%m/%d/%y",)),
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), ("%Y-%m-%d",)),
    (re.compile(r"\b(\d{2}-\d{2}-\d{2})\b"), ("%m-%d-%y",)),
    (re.compile(r"\b(\d{1,2}-[A-Za-z]{3}-\d{4})\b"), ("%d-%b-%Y",)),
    (re.compile(r"\b([A-Za-z]{3,9} \d{1,2},? \d{4})\b"), ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y")),
    (re.compile(r"\b(\d{1,2} [A-Za-z]{3,9} \d{4})\b"), ("%d %b %Y", "%d %B %Y")),
]


def parse_date(raw: str) -> Optional[date]:
    raw = collapse(raw)
    for pat, fmts in _DATE_PATTERNS:
        m = pat.search(raw)
        if not m:
            continue
        for f in fmts:
            try:
                return datetime.strptime(m.group(1), f).date()
            except ValueError:
                continue
    return None


def find_date(text: str, label_hint: str = "business date|audit date|date") -> Optional[date]:
    """Find the business date in report text, preferring one right after a label hint."""
    flat = collapse(text)
    hint = re.compile(rf"(?:{label_hint})\s*:?\s*([^\n]{{0,40}})", re.IGNORECASE)
    for m in hint.finditer(flat):
        d = parse_date(m.group(1))
        if d:
            return d
    return parse_date(flat)


# ----------------------------------------------------------------------------- input files
def pdf_text(path: Path) -> str:
    """Layout-preserving text for a PDF (pdfplumber), falling back to pypdf."""
    try:
        import pdfplumber
    except Exception:  # noqa: BLE001 - optional dependency problems (cryptography etc.)
        pdfplumber = None
    if pdfplumber is not None:
        try:
            with pdfplumber.open(str(path)) as pdf:
                pages = []
                for i, pg in enumerate(pdf.pages, start=1):
                    pages.append(f"===== PAGE {i} =====\n" + (pg.extract_text(layout=True, x_density=4.5, y_density=13) or ""))
                return "\n".join(pages)
        except Exception:  # noqa: BLE001
            pass
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n".join(f"===== PAGE {i} =====\n" + (pg.extract_text() or "")
                     for i, pg in enumerate(reader.pages, start=1))


def eml_attachments(path: Path, out_dir: Optional[Path] = None) -> list[Path]:
    """Save the PDF/XLSX/CSV attachments of an .eml file and return their paths."""
    msg = email.message_from_bytes(Path(path).read_bytes(), policy=policy.default)
    out_dir = out_dir or Path(tempfile.mkdtemp(prefix="pms_eml_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        fn = part.get_filename()
        ct = part.get_content_type()
        if not fn and ct not in ("application/pdf",):
            continue
        name = fn or f"attachment.{ct.split('/')[-1]}"
        if Path(name).suffix.lower() not in (".pdf", ".xlsx", ".xlsm", ".csv", ".txt"):
            continue
        p = out_dir / Path(name).name
        p.write_bytes(part.get_payload(decode=True) or b"")
        saved.append(p)
    return saved


def read_text(path: str | Path) -> str:
    """Return row-preserving plain text for PDF / EML / CSV / XLSX / TXT input."""
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".pdf":
        return pdf_text(p)
    if suf == ".eml":
        parts = []
        for att in eml_attachments(p):
            parts.append(f"===== ATTACHMENT {att.name} =====\n" + read_text(att))
        if not parts:
            raise ValueError(f"{p.name}: no PDF/XLSX/CSV attachment found in email")
        return "\n".join(parts)
    if suf in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
        rows = []
        for ws in wb.worksheets:
            rows.append(f"## sheet {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(cells):
                    rows.append("   ".join(cells))
        return "\n".join(rows)
    if suf == ".csv":
        with p.open(newline="", encoding="utf-8-sig") as fh:
            return "\n".join("   ".join(r) for r in csv.reader(fh))
    return p.read_text(encoding="utf-8", errors="replace")


def iter_lines(text: str) -> Iterable[tuple[int, str]]:
    """(line_no, stripped_line) for non-empty, non page-marker lines."""
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.startswith("===== PAGE") or line.startswith("===== ATTACHMENT"):
            continue
        yield n, line


class BaseParser(ABC):
    """Every PMS parser turns one report file into a DailyReport."""

    #: short identifier used in journal refs, e.g. "PEP"
    pms: str = "GENERIC"
    #: brand this PMS belongs to (documentation only)
    brand: str = ""
    #: file extensions this parser accepts
    extensions: tuple[str, ...] = (".pdf", ".eml", ".csv", ".xlsx", ".txt")
    #: report name(s) the parser expects, for error messages
    expects: str = ""

    def accepts(self, path: str | Path) -> bool:
        return Path(path).suffix.lower() in self.extensions

    @abstractmethod
    def parse(self, path: str | Path, property_code: str,
              business_date: Optional[date] = None) -> DailyReport: ...

    @staticmethod
    def lines_from_label_amount_pairs(pairs: Iterable[tuple[str, Decimal, str]],
                                      source: str) -> list[ReportLine]:
        return [ReportLine(label=lbl.strip(), amount=amt, section=sec, source=source)
                for lbl, amt, sec in pairs]
