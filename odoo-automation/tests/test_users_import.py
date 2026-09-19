"""Adding a hotel group's logins from a file.

The two things that matter: no password ever travels in the file, and a file with one bad row
writes nothing at all.
"""
import importlib

import pytest

from conftest import FIXTURES
from portal import users_import
from portal.auth import unusable_password_hash, verify_password

CONFIG_DIR = FIXTURES.parent.parent / "config"
PROPS = ["OKCON", "TXI47", "OKCAH"]


def head(*rows):
    return "\n".join(["username,role,properties,email,enabled"] + list(rows)) + "\n"


# ------------------------------------------------------------------ reading the file
def test_the_template_round_trips_through_its_own_reader():
    text = users_import.template(PROPS)
    p = users_import.parse(text, PROPS, existing_usernames=[])
    assert p.ok and [r.username for r in p.rows] == ["okcon.gm", "area.manager", "bookkeeper"]
    assert p.rows[1].properties == ["OKCON", "TXI47"]     # space separated in the template
    assert p.rows[2].role == "admin" and p.rows[2].properties == []


@pytest.mark.parametrize("separator", [",", ";", " ", ", ", "\n"])
def test_properties_take_whatever_separator_somebody_reached_for(separator):
    p = users_import.parse(head(f'gm,manager,"OKCON{separator}TXI47",gm@x.com,yes'), PROPS, [])
    assert p.ok, p.errors
    assert p.rows[0].properties == ["OKCON", "TXI47"]


def test_case_and_spacing_in_the_header_and_the_codes_do_not_matter():
    text = "Username, ROLE ,Properties,Email\n gm , Manager , okcon ,gm@x.com\n"
    p = users_import.parse(text, PROPS, [])
    assert p.ok, p.errors
    assert p.rows[0].username == "gm" and p.rows[0].properties == ["OKCON"]


def test_a_byte_order_mark_and_extra_columns_are_tolerated():
    text = "﻿username,role,properties,email,department,notes\ngm,manager,OKCON,gm@x.com,ops,hi\n"
    assert users_import.parse(text, PROPS, []).ok


def test_enabled_is_optional_and_reads_the_usual_words():
    p = users_import.parse(head("aa,manager,OKCON,a@x.com,",
                                "bb,manager,OKCON,b@x.com,No",
                                "cc,manager,OKCON,c@x.com,TRUE"), PROPS, [])
    assert p.ok, p.errors
    assert [r.enabled for r in p.rows] == [True, False, True]


def test_blank_lines_are_not_rows():
    p = users_import.parse(head("gm,manager,OKCON,gm@x.com,yes", ",,,,", "  ,,,,"), PROPS, [])
    assert p.ok and len(p.rows) == 1


# ------------------------------------------------------------------ refusing a bad file
@pytest.mark.parametrize("row, expected", [
    ("gm manager,manager,OKCON,gm@x.com,yes", "not a usable username"),
    (",manager,OKCON,gm@x.com,yes", "not a usable username"),
    ("gm,owner,OKCON,gm@x.com,yes", "has to be admin or manager"),
    ("gm,manager,OKCON,not-an-address,yes", "not an e-mail address"),
    ("gm,manager,OKCON,gm@x.com,maybe", "Use yes or no"),
    ("gm,manager,NOSUCH,gm@x.com,yes", "no property here is called NOSUCH"),
    ("gm,manager,,gm@x.com,yes", "a manager needs at least one property"),
    ("boss,admin,OKCON,boss@x.com,yes", "an admin already sees every property"),
])
def test_every_way_a_row_can_be_wrong_says_which_line_and_why(row, expected):
    p = users_import.parse(head(row), PROPS, [])
    assert not p.ok
    assert len(p.errors) == 1 and expected in p.errors[0]
    assert p.errors[0].startswith("Line 2"), p.errors[0]


def test_the_same_username_twice_names_the_earlier_line():
    p = users_import.parse(head("gm,manager,OKCON,a@x.com,yes",
                                "gm,manager,TXI47,b@x.com,yes"), PROPS, [])
    assert not p.ok and "already on line 2" in p.errors[0]


def test_you_cannot_change_your_own_account_from_a_file():
    """A typo here would lock the person out mid-import, with nobody left to fix it."""
    p = users_import.parse(head("admin,manager,OKCON,a@x.com,yes"), PROPS, ["admin"],
                           protect="admin")
    assert not p.ok and "the account doing the import" in p.errors[0]


def test_a_missing_header_says_what_is_needed():
    p = users_import.parse("name,job\nbob,manager\n", PROPS, [])
    assert not p.ok and "missing username, role, email" in p.errors[0]


def test_an_empty_file_and_a_header_with_nothing_under_it():
    assert "empty" in users_import.parse("", PROPS, []).errors[0]
    assert "only a header" in users_import.parse(head(), PROPS, []).errors[0]


def test_an_existing_username_is_an_update_not_an_error():
    p = users_import.parse(head("gm,manager,OKCON,gm@x.com,yes"), PROPS, existing_usernames=["gm"])
    assert p.ok and p.rows[0].exists and p.rows[0].action == "update"


# ------------------------------------------------------------------ the page
def make(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CONFIG_DIR / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CONFIG_DIR / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("PORTAL_STORE", "db")
    monkeypatch.setenv("PORTAL_BASE_URL", "https://portal.example.com")
    for k in ("SMTP_HOST", "SMTP_FROM", "ODOO_URL", "ODOO_API_KEY", "ODOO_TRANSPORT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, follow_redirects=False)
    assert c.post("/login", data={"username": "admin", "password": "admin"}).status_code == 303
    return c, app_module


def upload(c, text):
    return c.post("/admin/users/import", data={"send": "no"},
                  files=[("file", ("logins.csv", text.encode(), "text/csv"))])


def test_the_template_is_offered_with_this_deployments_own_codes(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch)
    r = c.get("/admin/users/template.csv")
    assert r.status_code == 200 and "logins.csv" in r.headers["content-disposition"]
    assert r.text.splitlines()[0] == "username,role,properties,email,enabled"
    assert sorted(app_module.state.props)[0] in r.text
    assert "Download the template" in c.get("/admin").text


def test_an_import_creates_accounts_nobody_can_sign_into_yet(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch)
    r = upload(c, head("okcon.gm,manager,OKCON,gm@champion.example,yes",
                       "office,admin,,office@champion.example,yes"))
    assert r.status_code == 200 and "2 logins" in r.text

    store = app_module.state.store
    made = {u["username"]: u for u in store.all_users()}
    assert made["okcon.gm"]["role"] == "manager" and made["okcon.gm"]["properties"] == "OKCON"
    assert made["office"]["role"] == "admin" and made["office"]["email"] == "office@champion.example"

    # No password was set, and none can be guessed.
    for name in ("okcon.gm", "office"):
        h = made[name]["password_hash"]
        assert h and not verify_password("", h) and not verify_password("okcon.gm", h)
    assert c.post("/login", data={"username": "okcon.gm", "password": ""}).status_code != 303

    # The link on the page is a working, single-use set-password link.
    token = r.text.split("/reset?token=")[1].split('"')[0]
    assert c.get(f"/reset?token={token}").status_code == 200
    done = c.post("/reset", data={"token": token, "password": "a-good-long-password",
                                  "confirm": "a-good-long-password"})
    assert done.status_code == 200 and "has been changed" in done.text
    # and the link is spent
    assert "expired or has already been used" in c.get(f"/reset?token={token}").text
    c.get("/logout")
    assert c.post("/login", data={"username": "okcon.gm",
                                  "password": "a-good-long-password"}).status_code == 303


def test_one_bad_row_writes_nothing_at_all(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch)
    before = {u["username"] for u in app_module.state.store.all_users()}
    r = upload(c, head("good.one,manager,OKCON,a@x.com,yes",
                       "bad.one,manager,NOSUCH,b@x.com,yes",
                       "good.two,manager,TXI47,c@x.com,yes"))
    assert r.status_code == 200 and "Nothing was imported" in r.text
    assert "Line 3" in r.text and "NOSUCH" in r.text
    assert {u["username"] for u in app_module.state.store.all_users()} == before


def test_an_import_can_update_without_touching_a_password(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch)
    store = app_module.state.store
    store.save_user("okcon.gm", "manager", ["OKCON"], password="already-chosen",
                    email="gm@champion.example")
    before = {u["username"]: u["password_hash"] for u in store.all_users()}["okcon.gm"]

    r = upload(c, head("okcon.gm,manager,OKCON TXI47,gm@champion.example,yes"))
    assert r.status_code == 200 and "updated" in r.text
    after = {u["username"]: u for u in store.all_users()}["okcon.gm"]
    assert after["properties"] == "OKCON,TXI47"
    assert after["password_hash"] == before, "an update must not lock somebody out"
    assert verify_password("already-chosen", after["password_hash"])


def test_the_links_are_e_mailed_when_there_is_a_mail_server(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch)
    sent = []
    from portal import mail
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "send_reporting",
                        lambda to, s, b: (sent.append((to, s, b)), (True, ""))[1])
    r = c.post("/admin/users/import", data={"send": "yes"},
               files=[("file", ("logins.csv",
                                head("okcon.gm,manager,OKCON,gm@champion.example,yes").encode(),
                                "text/csv"))])
    assert r.status_code == 200 and "e-mailed" in r.text
    (to, subject, body), = sent
    assert to == "gm@champion.example" and "login" in subject.lower()
    assert "/reset?token=" in body
    assert "password" in body.lower()
    # the message must not carry a password, only a way to choose one
    assert "okcon.gm" in body and "already-chosen" not in body


def test_importing_needs_the_database_store(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, PORTAL_STORE="yaml")
    assert upload(c, head("gm,manager,OKCON,gm@x.com,yes")).status_code == 400


def test_a_manager_cannot_import_logins(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch)
    c.get("/logout")
    c.post("/login", data={"username": "okcon", "password": "okcon"})
    assert upload(c, head("gm,manager,OKCON,gm@x.com,yes")).status_code in (303, 403)
    assert c.get("/admin/users/template.csv").status_code in (303, 403)
