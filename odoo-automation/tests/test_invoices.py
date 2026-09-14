from decimal import Decimal

from fake_odoo import FakeTransport
from pms_to_odoo.invoices import InvoiceData, InvoiceLine, create_vendor_bill
from pms_to_odoo.odoo_client import OdooClient


def sample_invoice():
    return InvoiceData(
        vendor_name="Acme Linen Supply", vendor_tax_id="12-3456789", invoice_number="INV-1001",
        invoice_date="2026-09-10", due_date="2026-10-10", subtotal="500.00", tax_amount="40.00",
        total="540.00",
        lines=[InvoiceLine(description="Bath towels", quantity="10", unit_price="30.00", amount="300.00",
                           category_hint="linen"),
               InvoiceLine(description="Delivery", unit_price="200.00", amount="200.00", category_hint="other")],
        confidence="high")


def test_arithmetic_check():
    inv = sample_invoice()
    assert inv.lines_total() == Decimal("500.00")
    assert inv.arithmetic_ok()
    inv.total = "600.00"
    assert not inv.arithmetic_ok()


def test_create_bill_is_draft_idempotent_and_attaches_file(tmp_path):
    pdf = tmp_path / "INV-1001.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    t = FakeTransport()
    client = OdooClient(t)
    res = create_vendor_bill(sample_invoice(), client, pdf, company="Example Hotel LLC",
                             expense_map={"linen": "6110"}, default_account="6900", journal_code="BILL")
    assert res.status == "created"
    bill = t.records["account.move"][0]
    assert bill["move_type"] == "in_invoice" and bill["state"] == "draft"
    assert bill["partner_id"] == 61 and bill["ref"] == "INV-1001" and bill["journal_id"] == 8
    lines = [c[2] for c in bill["invoice_line_ids"]]
    assert lines[0]["account_id"] == 26 and lines[0]["quantity"] == 10.0   # linen -> 6110
    assert lines[1]["account_id"] == 27                                    # default -> 6900
    assert t.records["ir.attachment"][0]["res_id"] == bill["id"]

    again = create_vendor_bill(sample_invoice(), client, pdf, company="Example Hotel LLC")
    assert again.status == "exists" and len(t.records["account.move"]) == 1


def test_unknown_vendor_without_create_flag():
    inv = sample_invoice()
    inv.vendor_name, inv.vendor_tax_id, inv.vendor_email = "Nobody Inc", None, None
    res = create_vendor_bill(inv, OdooClient(FakeTransport()), "x.pdf")
    assert res.status == "no-vendor"


def test_unknown_vendor_created_on_request():
    inv = sample_invoice()
    inv.vendor_name, inv.vendor_tax_id, inv.vendor_email = "Nobody Inc", None, None
    t = FakeTransport()
    res = create_vendor_bill(inv, OdooClient(t), "x.pdf", create_missing_vendor=True)
    assert res.status == "created"
    assert any(p["name"] == "Nobody Inc" for p in t.records["res.partner"])
