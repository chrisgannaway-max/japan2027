"""Invoice field extraction WITHOUT any AI service.

Works on invoices whose PDF contains text (most e-mailed invoices).  Scanned images have
no text layer; those need the optional Claude reader or manual entry.  Two layers:

1. Vendor templates (config/vendor_templates.yaml) - exact regexes for vendors you see
   every month.  A template applies when its ``match`` regex is found in the text.
2. Generic heuristics - labels like "Invoice #", "Invoice Date", "Due Date", "Total Due",
   "Amount Due", "Balance Due", the first substantial line as vendor name, EIN patterns.

Everything extracted is a *suggestion*: the portal shows it in a form the user confirms.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml

from ..parsers.base import collapse, parse_amount, parse_date, read_text
from .extract import InvoiceData, InvoiceLine

MONEY = r"\$?\s?-?\(?\d[\d,]*\.\d{2}\)?"
# an identifier that contains at least one digit (INV-1001, 2026-0912, A77/3) - never a plain word
TOKEN = r"[A-Za-z0-9][A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*"
DATE = r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.? \d{1,2},? \d{4}|\d{1,2} [A-Za-z]{3,9} \d{4})"

GENERIC = {
    "invoice_number": [r"invoice\s*(?:no\.?|number|num\.?|#)\s*[:.]?\s*#?\s*(" + TOKEN + ")",
                       r"invoice\s*[:#]\s*(" + TOKEN + ")",
                       r"\binv\.?\s*(?:no\.?|#)\s*[:.]?\s*(" + TOKEN + ")",
                       r"(?:reference|ref)\s*(?:no\.?|number|#)\s*[:.]?\s*(" + TOKEN + ")"],
    "invoice_date": [r"invoice\s*date\s*[:#]?\s*" + DATE, r"\bdate\s*(?:of\s*invoice)?\s*[:#]?\s*" + DATE, r"\bdated?\s*[:#]?\s*" + DATE],
    "due_date": [r"(?:due\s*date|payment\s*due|due\s*(?:on|by))\s*[:#]?\s*" + DATE, r"\bdue\s*[:#]?\s*" + DATE],
    "total": [r"(?:total\s*(?:amount\s*)?due|amount\s*due|balance\s*due|please\s*pay|grand\s*total|invoice\s*total|total\s*payable)\s*[:#]?\s*(?:USD|\$)?\s*(" + MONEY + ")",
              r"\btotal\s*[:#]?\s*(?:USD|\$)?\s*(" + MONEY + r")(?!\s*(?:tax|before))"],
    "subtotal": [r"sub\s*-?\s*total\s*[:#]?\s*(?:USD|\$)?\s*(" + MONEY + ")"],
    "tax_amount": [r"(?:sales\s*tax|tax(?:es)?|vat|gst|hst)\s*(?:\([^)]*\))?\s*[:#]?\s*(?:USD|\$)?\s*(" + MONEY + ")"],
    "vendor_tax_id": [r"(?:EIN|Tax\s*ID|TIN|Federal\s*ID|VAT\s*(?:No|Reg))\.?\s*[:#]?\s*([0-9]{2}-?[0-9]{7}|[A-Z]{2}[0-9A-Z]{8,12})"],
    "purchase_order": [r"(?:P\.?O\.?|purchase\s*order)\s*(?:no\.?|number|#)?\s*[:#]?\s*(" + TOKEN + ")"],
}
NOISE_FIRST_LINES = re.compile(r"^(invoice|statement|bill|page|date|tax invoice|receipt|original)\b", re.I)


def load_vendor_templates(path: Optional[Path]) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    data = yaml.safe_load(Path(path).read_text()) or {}
    return list(data.get("vendors", []))


def _first(patterns: list[str], text: str, flags=re.I) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m:
            return m.group(1).strip()
    return None


def _iso(raw: Optional[str]) -> Optional[str]:
    d = parse_date(raw) if raw else None
    return d.isoformat() if d else None


def _money(raw: Optional[str]) -> Optional[Decimal]:
    return parse_amount(raw) if raw else None


def guess_vendor(lines: list[str]) -> str:
    for line in lines[:12]:
        c = collapse(line)
        if len(c) < 3 or NOISE_FIRST_LINES.match(c) or re.fullmatch(r"[\d\W]+", c):
            continue
        if re.search(r"\b(invoice|total|date|due|amount|bill to|ship to)\b", c, re.I):
            continue
        return c[:80]
    return ""


def extract_with_rules(path: str | Path, templates: Optional[list[dict]] = None) -> InvoiceData:
    from .ocr import IMAGE_SUFFIXES, has_text_layer, ocr_available, ocr_text
    p = Path(path)
    source = "text"
    if p.suffix.lower() in IMAGE_SUFFIXES:
        text = ocr_text(p)
        source = "ocr"
    else:
        text = read_text(p)
        if not has_text_layer(text):               # scanned PDF: no text layer
            text = ocr_text(p)
            source = "ocr"
    if source == "ocr" and not has_text_layer(text):
        why = ("image could not be read by OCR" if ocr_available()
               else "OCR engine (tesseract) is not installed on the server")
        return InvoiceData(vendor_name="", invoice_number="", invoice_date=date.today().isoformat(), subtotal="0",
                           total="0", lines=[], confidence="low", review_notes=why)
    flat = collapse(text)
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("=====")]
    fields: dict[str, Optional[str]] = {}
    used_template = ""
    for t in templates or []:
        if re.search(t.get("match", "$^"), flat, re.I):
            used_template = t.get("name", "template")
            for key, pat in (t.get("fields") or {}).items():
                m = re.search(pat, flat, re.I)
                if m:
                    fields[key] = m.group(1).strip()
            if t.get("vendor_name"):
                fields["vendor_name"] = t["vendor_name"]
            break
    for key, pats in GENERIC.items():
        if not fields.get(key):
            fields[key] = _first(pats, flat)
    vendor = fields.get("vendor_name") or guess_vendor(lines)
    total = _money(fields.get("total")) or Decimal("0")
    subtotal = _money(fields.get("subtotal"))
    tax = _money(fields.get("tax_amount")) or Decimal("0")
    if subtotal is None:
        subtotal = total - tax if total else Decimal("0")
    inv_date = _iso(fields.get("invoice_date")) or ""
    notes = []
    if source == "ocr":
        notes.append("read by OCR (scanned image)")
    if used_template:
        notes.append(f"vendor template: {used_template}")
    missing = [k for k, v in (("vendor", vendor), ("invoice number", fields.get("invoice_number")),
                              ("invoice date", inv_date), ("total", total or None)) if not v]
    if missing:
        notes.append("could not find: " + ", ".join(missing))
    confidence = "high" if used_template and not missing else ("medium" if len(missing) <= 1 else "low")
    return InvoiceData(
        vendor_name=vendor, vendor_tax_id=fields.get("vendor_tax_id"), invoice_number=fields.get("invoice_number") or "",
        invoice_date=inv_date or date.today().isoformat(), due_date=_iso(fields.get("due_date")),
        subtotal=str(subtotal), tax_amount=str(tax), total=str(total), purchase_order=fields.get("purchase_order"),
        lines=[InvoiceLine(description="Invoice total", quantity="1", unit_price=str(subtotal), amount=str(subtotal),
                           category_hint="")] if total else [],
        confidence=confidence, review_notes="; ".join(notes) + (" (rules-based extraction, please verify)" if not used_template else ""),
    )
