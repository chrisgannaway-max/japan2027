"""The two switches that decide what this deployment is: hotel uploads, and invoices."""
import importlib

import pytest

from conftest import FIXTURES

CONFIG_DIR = FIXTURES.parent.parent / "config"


def make(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CONFIG_DIR / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CONFIG_DIR / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.delenv("ODOO_URL", raising=False)
    for k in ("HOTEL_UPLOADS", "INVOICES"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    return TestClient(app_module.app, follow_redirects=False), app_module


def login(c, u, p):
    assert c.post("/login", data={"username": u, "password": p}).status_code == 303


def test_by_default_uploads_are_on_and_invoices_are_off(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch)
    login(c, "admin", "admin")
    assert c.get("/upload").status_code == 200
    assert c.get("/invoices").status_code == 404
    nav = c.get("/upload").text
    assert "/upload" in nav and ">Invoices<" not in nav


def test_invoices_can_be_turned_on(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, INVOICES="on")
    login(c, "admin", "admin")
    assert c.get("/invoices").status_code == 200
    assert ">Invoices<" in c.get("/invoices").text


def test_uploads_off_closes_the_page_and_hides_the_link(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, HOTEL_UPLOADS="off")
    login(c, "admin", "admin")
    assert c.get("/upload").status_code == 404
    with open(FIXTURES / "pep_final_audit.txt", "rb") as fh:
        r = c.post("/upload", data={"property_code": "OKCON"},
                   files=[("files", ("a.txt", fh, "text/plain"))])
    assert r.status_code == 404                       # the POST is shut too, not just the link
    assert ">Night audit<" not in c.get("/").text


def test_a_manager_signing_in_with_uploads_off_is_told_what_to_do(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, HOTEL_UPLOADS="off")
    login(c, "okcon", "okcon")
    r = c.get("/")
    assert r.status_code == 200 and "sent by e-mail" in r.text     # not a redirect loop to /upload


def test_the_move_to_invoices_button_is_gone_when_invoices_are_off(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch)                    # invoices off by default
    login(c, "admin", "admin")
    assert c.post("/runs/1/to-invoice").status_code == 404
