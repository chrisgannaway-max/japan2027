"""PORTAL_STORE=db: configuration in the database, edited on the admin screens."""
import importlib

import pytest

from conftest import FIXTURES

CFG = FIXTURES.parent.parent / "config"


def make_client(tmp_path, monkeypatch, store: str, delivery: str = "download"):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CFG / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CFG / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("PORTAL_STORE", store)
    monkeypatch.setenv("DELIVERY_MODE", delivery)
    monkeypatch.delenv("ODOO_URL", raising=False)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    return TestClient(app_module.app, follow_redirects=False), app_module


def test_yaml_mode_admin_page_is_read_only(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch, "yaml")
    assert client.post("/login", data={"username": "admin", "password": "admin"}).status_code == 303
    r = client.get("/admin")
    assert r.status_code == 200 and ">files<" in r.text and "PORTAL_STORE=db" in r.text and "OKCON" in r.text
    assert client.post("/admin/import-yaml", data={}).status_code == 400          # not in db mode


def test_db_mode_seed_edit_and_use(tmp_path, monkeypatch):
    client, app_module = make_client(tmp_path, monkeypatch, "db")
    # empty database: nobody can log in yet, so seed it from the files through the store directly
    assert client.post("/login", data={"username": "admin", "password": "admin"}).status_code == 200
    counts = app_module.state.store.import_from_yaml(app_module.CONFIG, app_module.USERS)
    assert counts["properties"] >= 6 and counts["users"] >= 7
    app_module.state.reload_config()
    assert client.post("/login", data={"username": "admin", "password": "admin"}).status_code == 303
    r = client.get("/admin")
    assert r.status_code == 200 and ">database<" in r.text and "/admin/properties/OKCON" in r.text
    # edit a property: change the Odoo company and the mapping; the pipeline must pick it up
    prop = app_module.state.store.get_property("OKCON")
    form = {k: (prop[k] or "") for k in prop} | {"company": "Champion OKC LLC", "enabled": "1"}
    assert client.post("/admin/properties/OKCON", data=form).status_code == 303
    assert app_module.state.props["OKCON"]["company"] == "Champion OKC LLC"
    # invalid mapping YAML is rejected with a message, not saved
    bad = form | {"mapping_yaml": "rules:\n  - {match: 'x'}\n"}
    r = client.post("/admin/properties/OKCON", data=bad)
    assert r.status_code == 200 and "invalid" in r.text.lower()
    # add a login and use it
    assert client.post("/admin/users", data={"username": "gm2", "role": "manager", "properties": "OKCON", "password": "pw"}).status_code == 303
    client.get("/logout")
    assert client.post("/login", data={"username": "gm2", "password": "pw"}).status_code == 303
    with open(FIXTURES / "pep_final_audit.txt", "rb") as fh:
        r = client.post("/upload", data={"property_code": "OKCON"}, files=[("files", ("OKCON.txt", fh, "text/plain"))])
    assert r.status_code == 200 and "Balanced, ready to post" in r.text
    # the mapping the pipeline used came from the database-materialised file
    assert (tmp_path / "data" / "mappings" / "OKCON.yaml").exists()
    # a new property created from the form
    client.get("/logout")
    client.post("/login", data={"username": "admin", "password": "admin"})
    new = {k: "" for k in prop} | {"code": "TEST1", "name": "Test Hotel", "pms": "PEP", "pms_property_id": "TST1",
                                   "journal": "NA", "mapping_yaml": "journal: NA\nrules:\n  - {match: '.*', account: '9999'}\n", "enabled": "1"}
    assert client.post("/admin/properties/new", data=new).status_code == 303
    assert "TEST1" in app_module.state.props
    assert client.post("/admin/properties/TEST1", data=new | {"action": "delete"}).status_code == 303
    assert "TEST1" not in app_module.state.props


def test_delivery_mode_odoo_shows_send_button_when_configured(tmp_path, monkeypatch):
    client, app_module = make_client(tmp_path, monkeypatch, "yaml", delivery="odoo")
    client.post("/login", data={"username": "admin", "password": "admin"})
    r = client.get("/?day=2025-11-10")
    assert "Send to Odoo" not in r.text and "Download Odoo import CSV" in r.text     # no credentials -> download
    app_module.state.odoo_enabled = True
    r = client.get("/?day=2025-11-10")
    assert "Send to Odoo" in r.text
