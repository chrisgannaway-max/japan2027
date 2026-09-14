"""Convert a JournalEntry into Odoo ``account.move`` values and post it (idempotently)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import JournalEntry
from .odoo_client import OdooClient, OdooError


@dataclass
class PostResult:
    ref: str
    move_id: Optional[int]
    status: str          # "created", "posted", "exists", "dry-run"
    message: str = ""


def entry_to_odoo_values(entry: JournalEntry, client: OdooClient) -> dict:
    """Resolve codes to ids and build the create() payload for account.move."""
    company_id = client.company_id(entry.company_code)
    journal_id = client.journal_id(entry.journal_code, company_id)
    line_cmds = []
    for l in entry.lines:
        vals: dict = {
            "account_id": client.account_id(l.account_code, company_id),
            "name": l.name,
            "debit": float(l.debit),
            "credit": float(l.credit),
        }
        if l.partner_ref:
            partner = client.find_partner(name=l.partner_ref)
            if partner:
                vals["partner_id"] = partner["id"]
        if l.analytic_code:
            vals["analytic_distribution"] = {str(client.analytic_account_id(l.analytic_code)): 100}
        line_cmds.append([0, 0, vals])
    values: dict = {
        "move_type": "entry",
        "journal_id": journal_id,
        "date": entry.date.isoformat(),
        "ref": entry.ref,
        "narration": entry.narration,
        "line_ids": line_cmds,
    }
    if company_id:
        values["company_id"] = company_id
    return values


def post_entry(entry: JournalEntry, client: OdooClient, post: bool = False,
               dry_run: bool = False) -> PostResult:
    """Create the journal entry in Odoo unless one with the same ref already exists.

    ``post=False`` leaves the entry in *draft* so an accountant can review it in Odoo
    before it hits the ledger.  ``post=True`` calls action_post immediately.
    """
    if entry.imbalance != 0:
        raise OdooError(f"Refusing to send unbalanced entry {entry.ref} (diff {entry.imbalance})")
    if dry_run:
        return PostResult(entry.ref, None, "dry-run", "not sent to Odoo")
    company_id = client.company_id(entry.company_code)
    existing = client.find_move_by_ref(entry.ref, company_id)
    if existing:
        return PostResult(entry.ref, existing["id"], "exists",
                          f"already in Odoo as {existing['name']} ({existing['state']})")
    values = entry_to_odoo_values(entry, client)
    move_id = client.create_move(values, post=post)
    return PostResult(entry.ref, move_id, "posted" if post else "created")


def format_entry(entry: JournalEntry) -> str:
    """Human readable T-account style print-out for --dry-run and logs."""
    w = max([len(l.name) for l in entry.lines] + [12])
    out = [f"Journal entry {entry.ref}  date={entry.date}  journal={entry.journal_code}"
           + (f"  company={entry.company_code}" if entry.company_code else ""),
           f"  {'Account':<8} {'Description':<{w}} {'Debit':>14} {'Credit':>14}"]
    for l in entry.lines:
        d = f"{l.debit:,.2f}" if l.debit else ""
        c = f"{l.credit:,.2f}" if l.credit else ""
        out.append(f"  {l.account_code:<8} {l.name:<{w}} {d:>14} {c:>14}")
    out.append(f"  {'':<8} {'TOTAL':<{w}} {entry.total_debit:>14,.2f} {entry.total_credit:>14,.2f}")
    if entry.imbalance:
        out.append(f"  *** OUT OF BALANCE by {entry.imbalance:,.2f} ***")
    return "\n".join(out)
