"""PORTAL_STORE=db: configuration in the database, edited on the admin screens."""
import os
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
    # An empty database seeds itself on the first start, otherwise nobody could ever sign in:
    # the logins live in the database and the button that fills it is behind the login.
    assert len(app_module.state.store.all_users()) >= 7
    assert len(app_module.state.store.properties()) >= 6
    assert client.post("/login", data={"username": "admin", "password": "admin"}).status_code == 303
    # seeding is once-only: a second pass must not resurrect a login the admin deleted
    app_module.state.store.delete_user("okcon")
    app_module.state.reload_config()
    assert not any(u["username"] == "okcon" for u in app_module.state.store.all_users())
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


def test_odoo_autopost_defaults_to_draft_and_toggles(tmp_path, monkeypatch):
    client, app_module = make_client(tmp_path, monkeypatch, "db")
    assert app_module.state.autopost is False               # drafts unless somebody says otherwise
    assert client.post("/login", data={"username": "admin", "password": "admin"}).status_code == 303
    r = client.get("/admin")
    assert "drafts for review" in r.text

    assert client.post("/admin/odoo-autopost", data={"autopost": "yes"}).status_code == 303
    assert app_module.state.autopost is True
    assert "posted on arrival" in client.get("/admin").text

    assert client.post("/admin/odoo-autopost", data={}).status_code == 303   # unchecked box
    assert app_module.state.autopost is False


def test_autopost_pinned_on_the_host_cannot_be_changed_in_the_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_AUTOPOST", "yes")
    client, app_module = make_client(tmp_path, monkeypatch, "db")
    assert app_module.state.autopost is True and app_module.state.autopost_locked is True
    client.post("/login", data={"username": "admin", "password": "admin"})
    assert "cannot be changed here" in client.get("/admin").text
    r = client.post("/admin/odoo-autopost", data={})
    assert r.status_code == 303 and "Pinned" in r.headers["location"]
    assert app_module.state.autopost is True                # the environment still wins


@pytest.mark.skipif(not os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://")),
                    reason="needs a PostgreSQL DATABASE_URL")
def test_postgres_never_prepares_statements():
    """Supabase's transaction pooler (port 6543) hands each query whichever server connection is
    free, so a prepared statement from an earlier query may not be there -- and it fails under
    load rather than at once. A night audit is a few dozen queries a day; preparing them buys
    nothing and rules out a whole connection string."""
    from portal.sql import Pool
    c = Pool(os.environ["DATABASE_URL"]).connect()
    assert c._raw.prepare_threshold is None
    with c as conn:
        for _ in range(12):                 # past psycopg's default threshold of five
            conn.execute("SELECT 1").fetchone()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://")),
                    reason="needs a PostgreSQL DATABASE_URL")
def test_our_columns_are_only_our_own_schema():
    """A Supabase project ships with auth.users, which has an email column. Asking whether
    "users" has "email" without naming a schema answers about a table we never touch -- so the
    column would not be added to ours, and every login would fail looking for it."""
    import psycopg
    from portal.sql import Pool
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url) as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS auth")
        c.execute("DROP TABLE IF EXISTS auth.users")
        c.execute("CREATE TABLE auth.users (id int, email text, encrypted_password text)")
        c.execute("DROP TABLE IF EXISTS users")
        c.execute("CREATE TABLE users (username text)")
        c.commit()
    try:
        with Pool(url).connect() as conn:
            cols = conn.columns("users")
        assert cols == {"username"}                       # not auth.users's three
        assert "encrypted_password" not in cols
    finally:
        with psycopg.connect(url) as c:
            c.execute("DROP TABLE IF EXISTS users"); c.execute("DROP SCHEMA IF EXISTS auth CASCADE"); c.commit()
