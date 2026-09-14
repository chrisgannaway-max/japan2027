"""Vendor invoice intake.

Two readers behind one call, chosen by configuration:

* ``rules``  - no external service; regex templates + heuristics on the invoice text.
* ``claude`` - the optional AI reader (needs ANTHROPIC_API_KEY); reads scans and odd
               layouts and returns line items and a confidence.

``extract_invoice_auto`` uses Claude when INVOICE_READER=claude (or the key is set and
INVOICE_READER is unset), otherwise rules.  Either way the result is a suggestion that a
person confirms before a bill is created.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .extract import InvoiceData, InvoiceLine
from .rules_extract import extract_with_rules, load_vendor_templates
from .to_odoo import create_vendor_bill


def reader_in_use() -> str:
    pref = os.environ.get("INVOICE_READER", "").lower()
    if pref in ("rules", "claude"):
        return pref
    return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "rules"


def extract_invoice_auto(path: str | Path, templates_path: Optional[Path] = None) -> tuple[InvoiceData, str]:
    """(InvoiceData, reader name).  Falls back to rules if the AI reader fails."""
    if reader_in_use() == "claude":
        try:
            from .extract import extract_invoice
            return extract_invoice(path), "claude"
        except Exception as e:  # noqa: BLE001 - any API problem must not block the upload
            data = extract_with_rules(path, load_vendor_templates(templates_path))
            data.review_notes = f"AI reader failed ({type(e).__name__}); " + data.review_notes
            return data, "rules (fallback)"
    return extract_with_rules(path, load_vendor_templates(templates_path)), "rules"


__all__ = ["InvoiceData", "InvoiceLine", "extract_invoice_auto", "extract_with_rules", "create_vendor_bill",
           "reader_in_use", "load_vendor_templates"]
