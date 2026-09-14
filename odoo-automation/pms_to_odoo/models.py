"""Plain data structures shared by parsers, mapping and journal building."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class ReportLine:
    """One numeric line from a night-audit report.

    label:    text exactly as printed on the report ("Room Revenue", "Visa/MC", ...)
    amount:   signed value as printed (credits are NOT flipped here; mapping decides sides)
    section:  report section the line came from, when the parser can tell
              ("revenue", "tax", "settlement", "ledger", "statistics", or "" if unknown)
    source:   parser name / page / row hint for audit trail
    """

    label: str
    amount: Decimal
    section: str = ""
    source: str = ""


@dataclass
class DailyReport:
    """Normalised output of every PMS parser."""

    property_code: str
    business_date: date
    pms: str
    lines: list[ReportLine] = field(default_factory=list)
    # Non-financial statistics we may still want to capture (rooms sold, occupancy, ADR...).
    stats: dict[str, Decimal] = field(default_factory=dict)
    source_file: str = ""

    def total(self, section: str) -> Decimal:
        return sum((l.amount for l in self.lines if l.section == section), Decimal("0"))


@dataclass
class JournalLine:
    account_code: str
    name: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    partner_ref: Optional[str] = None       # e.g. AR customer for direct-bill lines
    analytic_code: Optional[str] = None     # analytic account (property / department)


@dataclass
class JournalEntry:
    ref: str                     # unique reference, used for idempotency ("PEP-ABC123-2026-09-13")
    date: date
    journal_code: str            # Odoo journal short code (e.g. "MISC" or a per-property journal)
    narration: str
    lines: list[JournalLine] = field(default_factory=list)
    company_code: Optional[str] = None

    @property
    def total_debit(self) -> Decimal:
        return sum((l.debit for l in self.lines), Decimal("0"))

    @property
    def total_credit(self) -> Decimal:
        return sum((l.credit for l in self.lines), Decimal("0"))

    @property
    def imbalance(self) -> Decimal:
        return self.total_debit - self.total_credit
