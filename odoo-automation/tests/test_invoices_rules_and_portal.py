"""No-AI invoice reading and the invoice screens of the portal."""
from decimal import Decimal

import pytest

from conftest import FIXTURES
from pms_to_odoo.export import BILL_HEADERS, bills_to_odoo_csv
from pms_to_odoo.invoices import extract_invoice_auto, extract_with_rules, reader_in_use

INV = FIXTURES / "invoices" / "acme_linen_INV-1001.txt"


def test_rules_reader_extracts_core_fields(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("INVOICE_READER", raising=False)
    assert reader_in_use() == "rules"
    data, reader = extract_invoice_auto(INV)
    assert reader == "rules"
    assert data.vendor_name == "ACME LINEN SUPPLY LLC"
    assert data.vendor_tax_id == "12-3456789"
    assert data.invoice_number == "INV-1001"
    assert data.invoice_date == "2026-09-10" and data.due_date == "2026-10-10"
    assert data.purchase_order == "4477"
    assert data.dec("total") == Decimal("540.00") and data.dec("tax_amount") == Decimal("40.00")
    assert data.dec("subtotal") == Decimal("500.00")
    assert data.arithmetic_ok()
    assert data.confidence == "medium" and "verify" in data.review_notes


def test_vendor_template_wins(tmp_path):
    (tmp_path / "vendors.yaml").write_text(
        "vendors:\n  - name: Acme\n    match: 'ACME LINEN'\n    vendor_name: Acme Linen Supply\n"
        "    fields:\n      invoice_number: 'Invoice #: ([A-Z0-9-]+)'\n      total: 'Total Due: +\\$([\\d,.]+)'\n")
    from pms_to_odoo.invoices import load_vendor_templates
    data = extract_with_rules(INV, load_vendor_templates(tmp_path / "vendors.yaml"))
    assert data.vendor_name == "Acme Linen Supply" and data.confidence == "high"
    assert data.dec("total") == Decimal("540.00")


def test_claude_reader_falls_back_to_rules_without_network(monkeypatch):
    monkeypatch.setenv("INVOICE_READER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-invalid")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")     # nothing listens here
    data, reader = extract_invoice_auto(INV)
    assert reader == "rules (fallback)" and data.invoice_number == "INV-1001"
    assert data.review_notes.startswith("AI reader failed")


def test_bills_csv():
    body = bills_to_odoo_csv([{"vendor_name": "Acme", "invoice_number": "INV-1", "invoice_date": "2026-09-10",
                               "due_date": "2026-10-10", "subtotal": "500", "tax_amount": "40", "total": "540",
                               "account_code": "5110", "property_code": "OKCON", "description": "Towels"}])
    lines = body.strip().splitlines()
    assert lines[0].split(",") == BILL_HEADERS
    assert lines[1].startswith("Acme,INV-1,2026-09-10,2026-10-10,Towels,5110,1,500.00,,")
    assert "total 540.00" in lines[1]


# ------------------------------------------------------------------ portal
@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg = FIXTURES.parent.parent / "config"
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(cfg / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(cfg / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("INVOICES", "on")        # this suite is about the invoice side
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("INVOICE_READER", raising=False)
    import importlib
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    return TestClient(app_module.app, follow_redirects=False)


def test_invoice_flow_without_ai_or_admin_steps(client):
    assert client.post("/login", data={"username": "okcon", "password": "okcon"}).status_code == 303
    r = client.get("/invoices")
    assert r.status_code == 200 and "Reader: <b>rules</b>" in r.text
    r = client.post("/invoices/upload", data={"property_code": "OKCON"},
                    files={"file": ("acme.txt", INV.read_bytes(), "text/plain")})
    assert r.status_code == 303
    inv_url = r.headers["location"]
    page = client.get(inv_url).text
    # every field found, account assigned by the vendor rule ("linen" -> 5110), so it is ready without anyone's help
    assert 'value="INV-1001"' in page and 'value="540.00"' in page and 'value="2026-09-10"' in page
    assert '<option value="5110" selected' in page and ">ready<" in page and "account by vendor-rule" in page
    assert client.get("/invoices/export/bills.csv").status_code == 403         # managers cannot export
    # a second upload of the same invoice is NOT ready: flagged as a possible duplicate
    r = client.post("/invoices/upload", data={"property_code": "OKCON"}, files={"file": ("acme2.txt", INV.read_bytes(), "text/plain")})
    dup_url = r.headers["location"]
    assert "possible duplicate" in client.get(dup_url).text and ">needs review<" in client.get(dup_url).text
    # admin just downloads: only the ready bill is exported, and the vendor's account is remembered
    client.get("/logout")
    assert client.post("/login", data={"username": "admin", "password": "admin"}).status_code == 303
    r = client.get("/invoices/export/bills.csv")
    assert r.status_code == 200 and r.text.splitlines()[0].split(",") == BILL_HEADERS
    assert len(r.text.strip().splitlines()) == 2 and "ACME LINEN SUPPLY LLC,INV-1001,2026-09-10,2026-10-10," in r.text
    assert ",5110,1,500.00" in r.text
    assert ">exported<" in client.get(inv_url).text
    import portal.app as app_module
    assert app_module.state.db.remembered_account("Acme Linen Supply LLC") == "5110"
    # admin can still reject the duplicate; nothing else was required of them
    inv_id = dup_url.rsplit("/", 1)[1]
    form = {"property_code": "OKCON", "vendor_name": "ACME LINEN SUPPLY LLC", "vendor_tax_id": "", "invoice_number": "INV-1001",
            "invoice_date": "2026-09-10", "due_date": "", "subtotal": "500", "tax_amount": "40", "total": "540",
            "account_code": "5110", "description": "", "notes": "", "action": "reject"}
    assert client.post(dup_url, data=form).status_code == 303
    assert ">rejected<" in client.get(dup_url).text
    assert client.post(f"{inv_url}/post").status_code == 400                    # no Odoo configured


def test_account_assigner_precedence(tmp_path):
    from pms_to_odoo.invoices.accounts import AccountAssigner
    rules = tmp_path / "v.yaml"
    rules.write_text("default_account: '6900'\nvendors:\n  - {match: 'linen', account: '5110'}\nkeywords:\n  - {match: 'repair', account: '6200'}\n")
    a = AccountAssigner(rules, remembered=lambda v: "5999" if "acme" in v.lower() else None)
    assert a.assign("Acme Linen") == ("5999", "remembered")
    assert a.assign("Best Linen Co") == ("5110", "vendor-rule")
    assert a.assign("Joe's Plumbing", "toilet repair") == ("6200", "keyword")
    assert a.assign("Unknown Vendor") == ("6900", "default")
    assert AccountAssigner(None).assign("x") == (None, "none")
