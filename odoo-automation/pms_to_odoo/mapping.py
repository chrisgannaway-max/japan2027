"""Map report line labels to GL accounts using a YAML rule file.

A mapping file looks like::

    property: HGI-EXAMPLE
    journal: NA            # Odoo journal short code for night-audit entries
    company: Example Hotel LLC
    analytic: HGI-EXAMPLE  # optional analytic account applied to every line
    tolerance: 0.01        # max absolute imbalance accepted before failing
    suspense_account: "9999"  # optional: unmapped lines go here instead of failing
    rules:
      - match: "^room revenue$"        # regex, case-insensitive, matched against the label
        account: "4000"
        side: credit                   # credit | debit | auto (auto = positive -> credit)
      - match: "^(visa|mastercard|visa/mc)$"
        account: "1120"
        side: debit
      - match: "^(direct bill|city ledger)"
        account: "1200"
        side: debit
        partner: "{label}"             # optional; placeholder {label} = the report label
      - match: "^guest ledger"
        section: ledger                # optional: only applies to lines from this section
        account: "1210"
        side: signed                   # signed = positive -> debit, negative -> credit
    ignore:
      - "^occupancy"                   # statistics we do not book
      - "^adr$"

Rules are evaluated in order; the first match wins.  Every non-ignored line MUST match
a rule (or a suspense account must be configured), otherwise mapping fails - this is
deliberate: a new revenue code appearing on a report should stop the run, not silently
land in the wrong account.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml

from .models import DailyReport, JournalEntry, JournalLine, ReportLine


class MappingError(ValueError):
    pass


@dataclass
class Rule:
    pattern: re.Pattern
    account: str
    side: str = "auto"
    section: Optional[str] = None
    partner: Optional[str] = None
    analytic: Optional[str] = None
    name: Optional[str] = None      # override the journal line description

    def matches(self, line: ReportLine) -> bool:
        if self.section and line.section and line.section != self.section:
            return False
        return bool(self.pattern.search(line.label.strip()))


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
    source: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "GLMapping":
        data = yaml.safe_load(Path(path).read_text()) or {}
        try:
            rules = [
                Rule(pattern=re.compile(r["match"], re.IGNORECASE), account=str(r["account"]),
                     side=r.get("side", "auto"), section=r.get("section"),
                     partner=r.get("partner"), analytic=r.get("analytic"), name=r.get("name"))
                for r in data.get("rules", [])
            ]
        except KeyError as e:
            raise MappingError(f"{path}: every rule needs 'match' and 'account' ({e})") from e
        for r in rules:
            if r.side not in ("auto", "credit", "debit", "signed"):
                raise MappingError(f"{path}: invalid side '{r.side}' for rule {r.pattern.pattern}")
        return cls(
            property_code=str(data.get("property", "")),
            journal=str(data.get("journal", "MISC")),
            rules=rules,
            ignore=[re.compile(p, re.IGNORECASE) for p in data.get("ignore", [])],
            company=data.get("company"),
            analytic=data.get("analytic"),
            tolerance=Decimal(str(data.get("tolerance", "0.01"))),
            suspense_account=(str(data["suspense_account"]) if data.get("suspense_account") else None),
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
            if self.is_ignored(line) or line.amount == 0:
                continue
            rule = self.find_rule(line)
            if rule is None:
                if self.suspense_account:
                    rule = Rule(pattern=re.compile(".*"), account=self.suspense_account,
                                side="signed", name=f"UNMAPPED: {line.label}")
                else:
                    unmapped.append(line.label)
                    continue
            debit, credit = self._sides(rule.side, line.amount)
            entry.lines.append(JournalLine(
                account_code=rule.account,
                name=rule.name or line.label,
                debit=debit, credit=credit,
                partner_ref=(rule.partner.format(label=line.label) if rule.partner else None),
                analytic_code=rule.analytic or self.analytic,
            ))
        if unmapped:
            raise MappingError(
                f"{len(unmapped)} report line(s) have no GL mapping in {self.source or 'mapping'}: "
                + ", ".join(repr(u) for u in unmapped)
                + ". Add a rule (or an 'ignore' pattern) for each."
            )
        if abs(entry.imbalance) > self.tolerance:
            raise MappingError(
                f"Entry {ref} does not balance: debits {entry.total_debit} vs credits "
                f"{entry.total_credit} (difference {entry.imbalance}). Check that every "
                f"settlement / ledger line on the report is mapped."
            )
        if entry.imbalance != 0 and self.suspense_account:
            # Rounding within tolerance: absorb the difference in suspense.
            d, c = self._sides("signed", -entry.imbalance)
            entry.lines.append(JournalLine(self.suspense_account, "Rounding", d, c))
        return entry

    @staticmethod
    def _sides(side: str, amount: Decimal) -> tuple[Decimal, Decimal]:
        amt = abs(amount)
        if side == "credit":
            return (Decimal("0"), amt) if amount >= 0 else (amt, Decimal("0"))
        if side == "debit":
            return (amt, Decimal("0")) if amount >= 0 else (Decimal("0"), amt)
        if side == "signed":  # positive -> debit
            return (amt, Decimal("0")) if amount >= 0 else (Decimal("0"), amt)
        # auto: revenue-style, positive -> credit
        return (Decimal("0"), amt) if amount >= 0 else (amt, Decimal("0"))

    def coverage_report(self, report: DailyReport) -> list[tuple[str, str, str]]:
        """(label, amount, account-or-status) for every line: handy for --dry-run output."""
        out = []
        for line in report.lines:
            if self.is_ignored(line):
                status = "ignored"
            else:
                rule = self.find_rule(line)
                status = rule.account if rule else (self.suspense_account or "UNMAPPED")
            out.append((line.label, f"{line.amount:,.2f}", status))
        return out
