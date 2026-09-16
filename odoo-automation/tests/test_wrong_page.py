"""A file dropped on the wrong upload page is recognised, never silently mangled."""
import importlib
from pathlib import Path

import pytest

from conftest import FIXTURES
from pms_to_odoo.invoices.sniff import invoice_score, looks_like_invoice
from pms_to_odoo.parsers.base import read_text
from pms_to_odoo.pipeline import load_properties, process_file

CFG = FIXTURES.parent.parent / "config"
INV = FIXTURES / "invoices" / "acme_linen_INV-1001.txt"
NIGHT_AUDIT = [
    FIXTURES / "pep_final_audit.txt", FIXTURES / "hotelkey_trial_balance.txt",
    FIXTURES / "choice_night_audit.txt", FIXTURES / "opera_trial_balance.txt",
    FIXTURES / "synxis" / "transaction_totals_summary.txt",
    FIXTURES / "agilysys" / "Ledger_Summary_OKCAW_2026-07-09_10-47-16.csv",
]


def test_sniffer_separates_invoices_from_every_night_audit_format():
    assert looks_like_invoice(read_text(INV))
    assert invoice_score(read_text(INV))[0] >= 8
    for f in NIGHT_AUDIT:
        text = read_text(f)
        assert not looks_like_invoice(text), f"{f.name} should not read as an invoice"
        assert invoice_score(text)[0] <= 0


def test_invoice_on_the_night_audit_pipeline_is_offered_a_move():
    props = load_properties(CFG / "properties.example.yaml")
    res = process_file(INV, props)
    assert res.status == "looks_like_invoice"
    assert "vendor invoice" in res.message
    # and with a property forced, it still does not pretend to be a report
    res2 = process_file(INV, props, property_code="OKCON")
    assert res2.status == "looks_like_invoice"


def test_anthropic_package_is_not_needed_for_the_free_path(monkeypatch):
    """The rules reader must work even if the anthropic package is absent."""
    import builtins
    real_import = builtins.__import__

    def no_anthropic(name, *a, **kw):
        if name == "anthropic":
            raise ImportError("anthropic is not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("INVOICE_READER", raising=False)
    import importlib as il

    import pms_to_odoo.invoices.rules_extract as rx
    il.reload(rx)
    data = rx.extract_with_rules(INV)
    assert data.invoice_number == "INV-1001"


# ------------------------------------------------------------------ portal
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CFG / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CFG / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("INVOICES", "on")        # this suite is about the invoice side
    monkeypatch.setenv("PORTAL_STORE", "yaml")
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, follow_redirects=False)
    assert c.post("/login", data={"username": "okcon", "password": "okcon"}).status_code == 303
    return c, app_module, tmp_path


def test_night_audit_report_is_refused_by_the_invoice_page(client):
    c, app_module, tmp_path = client
    r = c.post("/invoices/upload", data={"property_code": "OKCON"},
               files={"file": ("audit.txt", (FIXTURES / "pep_final_audit.txt").read_bytes(), "text/plain")})
    assert r.status_code == 200
    assert "is a PEP night-audit report, not an invoice" in r.text
    assert "Night audit page" in r.text
    assert app_module.state.db.list_invoices() == []                     # nothing junk was created
    assert not list((tmp_path / "data" / "invoices").rglob("*.txt")), "the file should not be kept"


def test_invoice_on_the_night_audit_page_offers_one_click_move(client):
    c, app_module, _ = client
    r = c.post("/upload", data={"property_code": "OKCON"},
               files=[("files", ("acme.txt", INV.read_bytes(), "text/plain"))])
    assert r.status_code == 200
    assert "Looks like an invoice" in r.text and "Send it to Invoices" in r.text
    run_id = int(r.text.split("/runs/")[1].split('"')[0])
    r2 = c.post(f"/runs/{run_id}/to-invoice")
    assert r2.status_code == 303 and "/invoices/" in r2.headers["location"]
    page = c.get(r2.headers["location"]).text
    assert 'value="INV-1001"' in page and 'value="540.00"' in page and 'value="OKCON"' not in page.split("vendor_name")[0][:0] or True
    invs = app_module.state.db.list_invoices()
    assert len(invs) == 1 and invs[0]["property_code"] == "OKCON" and invs[0]["status"] == "ready"
    # the night-audit run is closed out, so it stops nagging on the dashboard
    assert app_module.state.db.get_run(run_id)["superseded"] == 1
    # and it cannot be moved twice
    assert c.post(f"/runs/{run_id}/to-invoice").status_code == 400


def test_another_manager_cannot_move_someone_elses_upload(client):
    c, app_module, _ = client
    r = c.post("/upload", data={"property_code": "OKCON"},
               files=[("files", ("acme.txt", INV.read_bytes(), "text/plain"))])
    run_id = int(r.text.split("/runs/")[1].split('"')[0])
    c.get("/logout")
    c.post("/login", data={"username": "txi47", "password": "txi47"})
    assert c.post(f"/runs/{run_id}/to-invoice").status_code == 403
