"""Odoo-importable CSV of journal entries (fallback when the API is not used).

Layout follows Odoo's Journal Entries import (Accounting > Journal Entries > Import): the
first row of an entry carries the entry-level columns, continuation rows leave them blank.
Verify the column headers once against the import template of the client's Odoo version
(Accounting > Journal Entries > Favorites > Import records > download template).
"""
from __future__ import annotations

import csv
import io
from typing import Iterable

from .models import JournalEntry

HEADERS = ["Journal", "Date", "Reference", "Journal Items/Account", "Journal Items/Label",
           "Journal Items/Debit", "Journal Items/Credit", "Journal Items/Partner", "Journal Items/Analytic Distribution"]


def entries_to_odoo_csv(entries: Iterable[JournalEntry]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADERS)
    for e in entries:
        for i, l in enumerate(e.lines):
            head = [e.journal_code, e.date.isoformat(), e.ref] if i == 0 else ["", "", ""]
            w.writerow(head + [l.account_code, l.name, f"{l.debit:.2f}", f"{l.credit:.2f}",
                               l.partner_ref or "", f"{l.analytic_code}:100" if l.analytic_code else ""])
    return buf.getvalue()


def entries_to_flat_csv(entries: Iterable[JournalEntry]) -> str:
    """One row per line with the reference repeated: easier to pivot in Excel."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Reference", "Date", "Journal", "Account", "Label", "Debit", "Credit", "Partner", "Analytic"])
    for e in entries:
        for l in e.lines:
            w.writerow([e.ref, e.date.isoformat(), e.journal_code, l.account_code, l.name,
                        f"{l.debit:.2f}", f"{l.credit:.2f}", l.partner_ref or "", l.analytic_code or ""])
    return buf.getvalue()
