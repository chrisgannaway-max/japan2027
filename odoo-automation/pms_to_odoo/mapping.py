"""Map report lines to GL accounts using a YAML rule file.

A mapping file::

    property: OKCON
    journal: NA              # Odoo journal short code for night-audit entries
    company: Example Hotel LLC
    analytic: OKCON          # optional analytic account applied to every line
    tolerance: 0.01          # max absolute imbalance accepted before failing
    suspense_account: "9999" # optional: unmapped lines go here instead of failing
    book_transfers: false    # internal PMS ledger transfers are skipped by default
    rules:
      - match: "^guest room"            # regex on the label, case-insensitive
        account: "4000"
      - code: RM                        # or match the PMS transaction code exactly
        account: "4000"
      - match: "^(visa|master)"
        account: "1120"
        side: debit                     # credit | debit | signed | auto (default: by section)
      - match: "^direct bill"
        account: "1200"
        partner: "{label}"              # optional Odoo partner for the line
      - match: "net change$"
        section: ledger                 # optional: rule applies to this section only
        account: "1210"
    ignore:
      - "^occupancy"                    # labels we never book

Rules are evaluated in order; the first match wins.  ``side: auto`` (the default) books
by section - revenue/tax credit, settlement debit, ledger/expense signed (positive =
debit) - so most rules only need an account.  Every non-ignored line MUST match a rule
(or a suspense account must be configured), otherwise mapping fails: a new revenue code
on a report should stop the run, not silently land in the wrong account.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml

from .models import DailyReport, JournalEntry, JournalLine, ReportLine

SECTION_SIDE = {"revenue": "credit", "tax": "credit", "settlement": "debit",
                "ledger": "signed", "expense": "signed", "transfer": "signed", "": "credit"}


class MappingError(ValueError):
    pass


@dataclass
class Rule:
    account: str
    pattern: Optional[re.Pattern] = None
    code: Optional[str] = None
    side: str = "auto"
    section: Optional[str] = None
    partner: Optional[str] = None
    analytic: Optional[str] = None
    name: Optional[str] = None      # override the journal line description

    def matches(self, line: ReportLine) -> bool:
        if self.section is not None and line.section != self.section:
            return False
        if self.code and line.code and self.code.upper() == line.code.upper():
            return True
        if self.pattern is not None and self.pattern.search(line.label.strip()):
            return True
        return False

    def describe(self) -> str:
        return f"code={self.code}" if self.code and not self.pattern else f"match={self.pattern.pattern!r}"


@dataclass
class GLMapping:
    property_code: str
    journal: str
    rules: list[Rule]
    ignore: list[re.Pattern] = field(default_factory=list)
    company: Optional[str] = None
    analytic: Optional[str] = None
    tolerance: Decimal = Decimal("0.01")
    suspense_account: Optional[str] = None
    book_transfers: bool = False
    source: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "GLMapping":
        data = yaml.safe_load(Path(path).read_text()) or {}
        rules = []
        for i, r in enumerate(data.get("rules", []), start=1):
            if "account" not in r or not (r.get("match") or r.get("code")):
                raise MappingError(f"{path}: rule #{i} needs 'account' and either 'match' or 'code'")
            side = r.get("side", "auto")
            if side not in ("auto", "credit", "debit", "signed"):
                raise MappingError(f"{path}: rule #{i} has invalid side '{side}'")
            rules.append(Rule(
                account=str(r["account"]),
                pattern=re.compile(r["match"], re.IGNORECASE) if r.get("match") else None,
                code=str(r["code"]) if r.get("code") is not None else None,
                side=side, section=r.get("section"), partner=r.get("partner"),
                analytic=r.get("analytic"), name=r.get("name")))
        return cls(
            property_code=str(data.get("property", "")),
            journal=str(data.get("journal", "MISC")),
            rules=rules,
            ignore=[re.compile(p, re.IGNORECASE) for p in data.get("ignore", [])],
            company=data.get("company"),
            analytic=data.get("analytic"),
            tolerance=Decimal(str(data.get("tolerance", "0.01"))),
            suspense_account=(str(data["suspense_account"]) if data.get("suspense_account") else None),
            book_transfers=bool(data.get("book_transfers", False)),
            source=str(path),
        )

    # ------------------------------------------------------------------ core
    def find_rule(self, line: ReportLine) -> Optional[Rule]:
        for r in self.rules:
            if r.matches(line):
                return r
        return None

    def is_ignored(self, line: ReportLine) -> bool:
        return any(p.search(line.label.strip()) for p in self.ignore)

    def build_entry(self, report: DailyReport, narration: Optional[str] = None) -> JournalEntry:
        """Turn a parsed report into a balanced JournalEntry, or raise MappingError."""
        ref = f"{report.pms}-{report.property_code}-{report.business_date.isoformat()}"
        entry = JournalEntry(
            ref=ref, date=report.business_date, journal_code=self.journal,
            narration=narration or f"Night audit {report.property_code} {report.business_date}",
            company_code=self.company,
        )
        unmapped: list[str] = []
        for line in report.lines:
            if line.section == "transfer" and not self.book_transfers:
                continue
            if self.is_ignored(line) or line.amount == 0:
                continue
            rule = self.find_rule(line)
            if rule is None:
                if self.suspense_account:
                    rule = Rule(account=self.suspense_account, side="signed", name=f"UNMAPPED: {line.label}")
                else:
                    unmapped.append(f"{line.label}" + (f" [{line.code}]" if line.code else "") + f" ({line.section or 'no section'})")
                    continue
            side = SECTION_SIDE.get(line.section, "credit") if rule.side == "auto" else rule.side
            debit, credit = self._sides(side, line.amount)
            entry.lines.append(JournalLine(
                account_code=rule.account,
                name=rule.name or line.label,
                debit=debit, credit=credit,
                partner_ref=(rule.partner.format(label=line.label) if rule.partner else None),
                analytic_code=rule.analytic or self.analytic,
            ))
        if unmapped:
            raise MappingError(
                f"{len(unmapped)} report line(s) have no GL mapping in {self.source or 'mapping'}:\n  - "
                + "\n  - ".join(unmapped)
                + "\nAdd a rule (or an 'ignore' pattern) for each."
            )
        if abs(entry.imbalance) > self.tolerance:
            raise MappingError(
                f"Entry {ref} does not balance: debits {entry.total_debit} vs credits "
                f"{entry.total_credit} (difference {entry.imbalance}). Check that every "
                f"settlement / ledger line on the report is mapped and that no internal "
                f"transfer is booked twice."
            )
        if entry.imbalance != 0 and self.suspense_account:
            d, c = self._sides("signed", -entry.imbalance)
            entry.lines.append(JournalLine(self.suspense_account, "Rounding", d, c))
        return entry

    @staticmethod
    def _sides(side: str, amount: Decimal) -> tuple[Decimal, Decimal]:
        amt = abs(amount)
        if side == "credit":
            return (Decimal("0"), amt) if amount >= 0 else (amt, Decimal("0"))
        # debit and signed: positive -> debit
        return (amt, Decimal("0")) if amount >= 0 else (Decimal("0"), amt)

    def unmapped_lines(self, report: DailyReport) -> list[ReportLine]:
        """The lines that would make build_entry fail: no rule, not ignored, not zero."""
        out = []
        for line in report.lines:
            if line.section == "transfer" and not self.book_transfers:
                continue
            if self.is_ignored(line) or line.amount == 0:
                continue
            if self.find_rule(line) is None:
                out.append(line)
        return out

    def coverage_report(self, report: DailyReport) -> list[tuple[str, str, str, str]]:
        """(label, section, amount, account-or-status) for every line: for --dry-run output."""
        out = []
        for line in report.lines:
            if line.section == "transfer" and not self.book_transfers:
                status = "transfer (not booked)"
            elif self.is_ignored(line):
                status = "ignored"
            elif line.amount == 0:
                status = "zero"
            else:
                rule = self.find_rule(line)
                status = rule.account if rule else (f"{self.suspense_account} (suspense)" if self.suspense_account else "UNMAPPED")
            lbl = line.label + (f" [{line.code}]" if line.code else "")
            out.append((lbl, line.section, f"{line.amount:,.2f}", status))
        return out
