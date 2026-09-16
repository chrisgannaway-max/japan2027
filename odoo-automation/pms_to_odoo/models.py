"""Plain data structures shared by parsers, mapping and journal building.

Sign conventions every parser normalises to (so one mapping engine fits all PMSs):

    section      amount means                                  default GL side
    ---------    ------------------------------------------    -----------------------
    revenue      revenue earned today (allowances negative)    credit
    tax          tax collected today                           credit
    settlement   money received today (refunds negative)       debit
    ledger       change in a PMS ledger balance today          signed (+ = debit)
                 (guest ledger, AR/city ledger, deposits)
    expense      charges that are expenses (paid-outs)         signed (+ = debit)
    transfer     internal moves between PMS ledgers            NOT BOOKED
                 (direct bill transfers, AR credit applications)
    ""           unknown - mapping rule must give a side       credit
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

SECTIONS = ("revenue", "tax", "settlement", "ledger", "expense", "transfer", "")


@dataclass
class ReportLine:
    """One numeric line from a night-audit report."""

    label: str
    amount: Decimal
    section: str = ""
    source: str = ""          # parser:file:line for the audit trail
    code: str = ""            # PMS transaction code when the report prints one (RM, T1, 9002, RR)
    gl_code: str = ""         # GL account printed by the PMS itself, when configured there (Agilysys, SynXis)


@dataclass
class DailyReport:
    """Normalised output of every PMS parser."""

    property_code: str
    business_date: date
    pms: str
    lines: list[ReportLine] = field(default_factory=list)
    # Non-financial statistics and control totals (rooms sold, occupancy, report totals...).
    stats: dict[str, Decimal] = field(default_factory=dict)
    source_file: str = ""
    pms_property_id: str = ""     # id printed on the report (Hotel ID OKCON, Property Code TXI47)
    property_name: str = ""
    warnings: list[str] = field(default_factory=list)
    recognised: bool = True       # False when the file is not the report this parser books
    companions: list[str] = field(default_factory=list)   # other files merged into this report
    #: set when this is one half of a report that needs both halves (SynXis sends the revenue
    #: and the ledger movements as separate files).  Names the half that is missing.
    awaiting_companion: str = ""

    def total(self, section: str) -> Decimal:
        return sum((l.amount for l in self.lines if l.section == section), Decimal("0"))

    def add(self, label: str, amount: Decimal, section: str, source: str = "", code: str = "") -> ReportLine:
        line = ReportLine(label=label.strip(), amount=amount, section=section, source=source, code=code)
        self.lines.append(line)
        return line


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
    ref: str                     # unique reference, used for idempotency ("PEP-OKCON-2025-11-10")
    date: date
    journal_code: str            # Odoo journal short code
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
