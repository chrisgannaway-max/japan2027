"""The Settings page: what the timer will do, and the buttons that do it now.

Nobody should have to wait ten minutes -- or until six tomorrow morning -- to find out whether
the plumbing works.
"""
import importlib

import pytest

from conftest import FIXTURES

CONFIG_DIR = FIXTURES.parent.parent / "config"


def make(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CONFIG_DIR / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CONFIG_DIR / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("PORTAL_STORE", "db")
    for k in ("ODOO_URL", "ODOO_API_KEY", "ODOO_TRANSPORT", "DELIVERY_MODE",
              "SCHEDULER", "REPORT_AT", "REPORT_TO"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # The demo Odoo keeps its books for the life of the process, which is what we want in
    # production and not between tests.
    import pms_to_odoo.odoo_demo as odoo_demo
    importlib.reload(odoo_demo)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, follow_redirects=False)
    assert c.post("/login", data={"username": "admin", "password": "admin"}).status_code == 303
    return c, app_module


def test_settings_says_when_the_list_goes_and_that_nothing_is_running(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch)
    body = c.get("/admin").text
    assert "On a timer:" in body and "off</span>" in body
    assert "SCHEDULER=on" in body                      # and how to change that
    assert "06:00" in body and "the latest property cut-off" in body
    assert "nowhere yet" in body                       # no REPORT_TO set
    assert "Send the morning list now" in body and "Send waiting nights to Odoo now" in body


def test_the_send_time_can_be_changed_from_the_page(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch)
    r = c.post("/admin/report-at", data={"report_at": "07:15"})
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert "07:15" in c.get("/admin").text
    assert app_module.scheduler.report_time(app_module.state)[1] == "set on the Settings page"

    assert "Give+a+time" in c.post("/admin/report-at", data={"report_at": "25:00"}).headers["location"]
    assert "07:15" in c.get("/admin").text             # the bad one changed nothing

    c.post("/admin/report-at", data={"report_at": ""})  # back to the derived time
    assert app_module.scheduler.report_time(app_module.state)[1] == "the latest property cut-off"


def test_a_pinned_send_time_cannot_be_changed_in_the_browser(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, REPORT_AT="05:45")
    assert "05:45" in c.get("/admin").text and "Pinned on the host" in c.get("/admin").text
    r = c.post("/admin/report-at", data={"report_at": "09:00"})
    assert "pinned" in r.headers["location"]


def test_the_queue_button_refuses_politely_when_there_is_no_odoo(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch)
    r = c.post("/admin/queue/run")
    assert r.status_code == 303 and "DELIVERY_MODE" in r.headers["location"]


def test_an_upload_goes_straight_over_when_odoo_is_there(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch, DELIVERY_MODE="odoo", ODOO_TRANSPORT="demo")
    assert app_module.state.odoo_demo and app_module.state.odoo_enabled
    assert "DEMO" in c.get("/admin").text               # never mistaken for the real thing

    with open(FIXTURES / "pep_final_audit.txt", "rb") as fh:
        c.post("/upload", data={"property_code": "OKCON"},
               files=[("files", ("OKCON_final_audit.txt", fh, "text/plain"))])
    assert "<b>0</b> night" in c.get("/admin").text      # nothing left queued
    assert "Nothing+was+waiting" in c.post("/admin/queue/run").headers["location"]
    assert "posted" in c.get("/runs/1").text


def test_the_button_clears_a_night_that_odoo_refused_earlier(tmp_path, monkeypatch):
    """The case it exists for: Odoo was unreachable at 3am, somebody presses this at nine."""
    c, app_module = make(tmp_path, monkeypatch, DELIVERY_MODE="odoo",
                         ODOO_URL="http://127.0.0.1:1", ODOO_API_KEY="nope")
    with open(FIXTURES / "pep_final_audit.txt", "rb") as fh:
        c.post("/upload", data={"property_code": "OKCON"},
               files=[("files", ("OKCON_final_audit.txt", fh, "text/plain"))])
    assert "<b>1</b> night" in c.get("/admin").text      # it did not get there

    monkeypatch.setenv("ODOO_TRANSPORT", "demo")         # the connection comes back
    r = c.post("/admin/queue/run")
    assert r.status_code == 303 and "1+sent+to+Odoo" in r.headers["location"], r.headers["location"]
    assert "<b>0</b> night" in c.get("/admin").text
