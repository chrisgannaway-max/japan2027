"""Create a *draft* vendor bill (account.move, move_type=in_invoice) from InvoiceData."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from ..odoo_client import OdooClient
from .extract import InvoiceData


@dataclass
class BillResult:
    move_id: Optional[int]
    status: str            # "created", "exists", "dry-run"
    partner_name: str
    message: str = ""


def load_expense_map(path: Optional[str | Path]) -> dict[str, str]:
    """category_hint (lower-case) -> GL account code.  Missing file -> empty map."""
    if not path or not Path(path).exists():
        return {}
    data = yaml.safe_load(Path(path).read_text()) or {}
    return {str(k).lower(): str(v) for k, v in (data.get("categories") or {}).items()}


def bill_values(inv: InvoiceData, client: OdooClient, partner_id: int,
                expense_map: dict[str, str], company_id: Optional[int],
                default_account: Optional[str], journal_code: Optional[str]) -> dict:
    line_cmds = []
    for l in inv.lines:
        vals: dict = {
            "name": l.description,
            "quantity": float(l.quantity.replace(",", "") or 1),
            "price_unit": float(l.unit_price.replace(",", "").replace("$", "") or 0),
        }
        code = expense_map.get(l.category_hint.lower()) or default_account
        if code:
            vals["account_id"] = client.account_id(code, company_id)
        line_cmds.append([0, 0, vals])
    values: dict = {
        "move_type": "in_invoice",
        "partner_id": partner_id,
        "invoice_date": inv.invoice_date,
        "ref": inv.invoice_number,                     # vendor reference (dedupe key)
        "invoice_line_ids": line_cmds,
        "narration": (f"Auto-extracted (confidence: {inv.confidence}). {inv.review_notes}").strip(),
    }
    if inv.due_date:
        values["invoice_date_due"] = inv.due_date
    if inv.purchase_order:
        values["invoice_origin"] = inv.purchase_order
    if company_id:
        values["company_id"] = company_id
    if journal_code:
        values["journal_id"] = client.journal_id(journal_code, company_id)
    return values


def create_vendor_bill(inv: InvoiceData, client: OdooClient, source_file: str | Path,
                       company: Optional[str] = None, expense_map: Optional[dict[str, str]] = None,
                       default_account: Optional[str] = None, journal_code: Optional[str] = None,
                       create_missing_vendor: bool = False, dry_run: bool = False) -> BillResult:
    """Idempotent on (vendor, invoice number).  Always leaves the bill in draft."""
    expense_map = expense_map or {}
    if dry_run:
        return BillResult(None, "dry-run", inv.vendor_name, "not sent to Odoo")
    company_id = client.company_id(company)
    partner = client.find_partner(name=inv.vendor_name, vat=inv.vendor_tax_id, email=inv.vendor_email)
    if not partner:
        if not create_missing_vendor:
            return BillResult(None, "no-vendor", inv.vendor_name,
                              f"Vendor '{inv.vendor_name}' not in Odoo; create it or rerun with --create-vendor")
        pid = client.create("res.partner", {
            "name": inv.vendor_name, "is_company": True, "supplier_rank": 1,
            "vat": inv.vendor_tax_id or False, "email": inv.vendor_email or False,
            "street": (inv.vendor_address or "")[:128] or False,
        })
        partner = {"id": pid, "name": inv.vendor_name}
    dupes = client.search_read("account.move", [
        ("move_type", "=", "in_invoice"), ("partner_id", "=", partner["id"]),
        ("ref", "=", inv.invoice_number)], ["id", "name", "state"], limit=1)
    if dupes:
        return BillResult(dupes[0]["id"], "exists", partner["name"],
                          f"bill {inv.invoice_number} already exists as {dupes[0]['name']} ({dupes[0]['state']})")
    values = bill_values(inv, client, partner["id"], expense_map, company_id, default_account, journal_code)
    move_id = client.create_move(values, post=False)
    src = Path(source_file)
    if src.exists():
        client.attach_file("account.move", move_id, src.name,
                           base64.b64encode(src.read_bytes()).decode("ascii"),
                           mimetype="application/pdf" if src.suffix.lower() == ".pdf" else "image/jpeg")
    return BillResult(move_id, "created", partner["name"],
                      f"draft bill created (confidence {inv.confidence})")
