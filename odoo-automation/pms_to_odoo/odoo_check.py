"""Ask an Odoo server everything we are going to need, before a night depends on the answer.

Sending the first night is a bad way to discover that the journal is called something else,
that half the GL codes in the mapping do not exist, or that the analytic module was never
installed. Each of those arrives as a separate failure on a separate morning, and each one
costs a round trip to somebody at the client.

This asks all of it in one pass and reports everything at once: reachable, authenticated,
which analytic field this version wants, and for every property -- its company, its journal,
its analytic account, and which of its mapped GL codes are missing.

It creates nothing. It can be run against a client's live server without leaving a trace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .mapping import GLMapping, MappingError
from .odoo_client import OdooClient, OdooError
from .pipeline import resolve


@dataclass
class PropertyCheck:
    code: str
    ok: bool = True
    company: str = ""
    journal: str = ""
    analytic: str = ""
    missing_accounts: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class Check:
    reachable: bool = False
    why: str = ""
    analytic_field: str = ""
    companies: int = 0
    journals: int = 0
    accounts: int = 0
    properties: list[PropertyCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.reachable and all(p.ok for p in self.properties)

    @property
    def summary(self) -> str:
        if not self.reachable:
            return f"Could not use this Odoo: {self.why}"
        bad = [p.code for p in self.properties if not p.ok]
        if bad:
            return f"Reached Odoo, but {len(bad)} propert{'y' if len(bad) == 1 else 'ies'} " \
                   f"would not post yet: {', '.join(bad)}."
        if not self.properties:
            return "Reached Odoo. No properties are configured to check against it."
        return f"Reached Odoo. All {len(self.properties)} properties look ready to post."


def run(client: OdooClient, props: dict) -> Check:
    """`props` is the portal's property dict, each carrying the path to its GL mapping."""
    out = Check()
    try:
        out.accounts = len(client.search_read("account.account", [], ["id"]))
        out.companies = len(client.search_read("res.company", [], ["id"]))
        out.journals = len(client.search_read("account.journal", [], ["id"]))
        out.analytic_field = client.analytic_field() or "none installed"
        out.reachable = True
    except OdooError as e:
        out.why = str(e)
        return out
    except Exception as e:                       # noqa: BLE001 - a check must never raise
        out.why = f"{type(e).__name__}: {e}"
        return out

    for code, prop in sorted(props.items()):
        pc = PropertyCheck(code=code)
        mapping = _mapping(prop)
        if mapping is None:
            pc.problems.append("This property has no GL mapping to check.")
            pc.ok = False
            out.properties.append(pc)
            continue

        # Resolve exactly as build_entry does -- from the mapping.  The property's own
        # company / journal / analytic columns are used by the invoice side; if one is set
        # and disagrees, say so, because a screen showing one value while the entry carries
        # another is a trap for whoever fills it in.
        company_id = None
        for what, mine, theirs in (("company", mapping.company, prop.get("company")),
                                   ("journal", mapping.journal, prop.get("journal")),
                                   ("analytic", mapping.analytic, prop.get("analytic"))):
            if theirs and mine and str(theirs) != str(mine):
                pc.problems.append(
                    f"The property page says the {what} is '{theirs}' but the mapping posts to "
                    f"'{mine}'. The mapping is what an entry carries.")

        try:
            if mapping.company:
                company_id = client.company_id(mapping.company)
                pc.company = mapping.company
        except OdooError as e:
            pc.problems.append(str(e))
        try:
            if mapping.journal:
                client.journal_id(mapping.journal, company_id)
                pc.journal = mapping.journal
            else:
                pc.problems.append("The mapping sets no journal, so an entry has nowhere to go.")
        except OdooError as e:
            pc.problems.append(str(e))
        try:
            if mapping.analytic and out.analytic_field != "none installed":
                client.analytic_account_id(mapping.analytic)
                pc.analytic = mapping.analytic
        except OdooError as e:
            pc.problems.append(str(e))

        codes = _mapped_codes(mapping)
        if codes:
            try:
                client.prefetch_accounts(codes, company_id)
                pc.missing_accounts = [c for c in codes
                                       if ("account", c, company_id) not in client._cache]
            except OdooError as e:
                pc.problems.append(str(e))
        pc.ok = not pc.problems and not pc.missing_accounts
        out.properties.append(pc)
    return out


def _mapping(prop: dict) -> Optional[GLMapping]:
    """The same file the posting path loads -- resolved the same way, relative to the config."""
    try:
        path = resolve(prop, "gl_mapping")
        return GLMapping.load(path) if path else None
    except (MappingError, KeyError, OSError, TypeError):
        return None


def _mapped_codes(mapping: GLMapping) -> list[str]:
    """Every GL code this mapping can produce, so all of them are asked about at once."""
    codes = [str(r.account) for r in (mapping.rules or []) if r.account]
    if mapping.suspense_account:
        codes.append(str(mapping.suspense_account))
    return list(dict.fromkeys(codes))
