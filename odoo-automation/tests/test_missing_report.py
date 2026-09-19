"""The report that shows which properties did not upload."""
import importlib
from datetime import date, timedelta

# The grid's "today" is the hotels' today, which is not the container's when the
# container runs in UTC and it is already tomorrow there.
from portal.clock import today as today_

import pytest

from conftest import FIXTURES

CFG = FIXTURES.parent.parent / "config"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CFG / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CFG / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("PORTAL_STORE", "yaml")
    monkeypatch.delenv("ODOO_URL", raising=False)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, follow_redirects=False)
    return c, app_module


def seed(app_module, property_code, business_date, status="ok"):
    """A run as if that property had uploaded for that night."""
    from pms_to_odoo.pipeline import RunResult
    res = RunResult(status=status, property_code=property_code, pms="PEP",
                    business_date=date.fromisoformat(business_date), ref=f"PEP-{property_code}-{business_date}")
    return app_module.state.db.add_run(uploaded_by="gm", property_code=property_code, business_date=business_date,
                                       pms="PEP", ref=res.ref, status=status, message="", file_name="x.pdf",
                                       stored_path="/tmp/x.pdf", result_json=res.to_json())


def test_report_is_admin_only(client):
    c, _ = client
    assert c.get("/missing").status_code == 303                 # not logged in
    c.post("/login", data={"username": "okcon", "password": "okcon"})
    assert c.get("/missing").status_code == 403                 # managers cannot see it
    assert c.get("/missing.csv").status_code == 403


def test_grid_flags_who_missed_last_night(client):
    c, app_module = client
    last_night = (today_() - timedelta(days=1)).isoformat()
    two_nights = (today_() - timedelta(days=2)).isoformat()
    seed(app_module, "OKCON", last_night)                        # fine
    seed(app_module, "TXI47", last_night, status="unmapped")     # uploaded but stuck
    seed(app_module, "OKCMD", two_nights)                        # reported before, silent last night
    c.post("/login", data={"username": "admin", "password": "admin"})
    page = c.get("/missing?days=3").text
    assert "1 did not upload" in page and "OKCMD" in page
    assert "1 uploaded but did not go through" in page and "TXI47" in page
    grid = app_module._coverage_grid(3)
    cells = {r["code"]: [x["state"] for x in r["cells"]] for r in grid["rows"]}
    assert cells["OKCON"][-1] == "ok"
    assert cells["TXI47"][-1] == "problem"
    assert cells["OKCMD"][-1] == "missing" and cells["OKCMD"][-2] == "ok"
    # a property that has never uploaded is not painted red for history it was never part of
    assert set(cells["CANDLEWOOD-MOORE"]) == {"before"}
    assert grid["missing_last_night"] == ["OKCMD"]


def test_a_property_is_not_missing_before_its_first_upload(client):
    c, app_module = client
    today = today_()
    seed(app_module, "OKCON", (today - timedelta(days=2)).isoformat())
    seed(app_module, "OKCON", (today - timedelta(days=1)).isoformat())
    grid = app_module._coverage_grid(5)
    states = [x["state"] for x in next(r for r in grid["rows"] if r["code"] == "OKCON")["cells"]]
    assert states == ["before", "before", "before", "ok", "ok"]


def test_everyone_reported_message(client):
    c, app_module = client
    last_night = (today_() - timedelta(days=1)).isoformat()
    for code in app_module.state.props:
        seed(app_module, code, last_night)
    c.post("/login", data={"username": "admin", "password": "admin"})
    page = c.get("/missing?days=1").text
    assert "Everyone reported" in page and "did not upload" not in page


def test_csv_lists_every_property_and_night(client):
    c, app_module = client
    last_night = (today_() - timedelta(days=1)).isoformat()
    seed(app_module, "OKCON", last_night)
    c.post("/login", data={"username": "admin", "password": "admin"})
    r = c.get("/missing.csv?days=2")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("Property,Name,PMS,Business date,State")
    assert len(lines) - 1 == len(app_module.state.props) * 2
    assert any(l.startswith("OKCON,") and last_night in l and ",ok," in l for l in lines)
