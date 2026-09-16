"""The posting queue: balanced nights reaching Odoo on their own, one property at a time."""
from pathlib import Path

import pytest

from conftest import FIXTURES
from fake_odoo import FakeTransport
from portal import storage
from portal.db import Database
from portal.intake import ingest, message_from_postmark
from portal.poster import MAX_ATTEMPTS, post_due
from pms_to_odoo.odoo_client import OdooClient, OdooError
from pms_to_odoo.pipeline import load_properties, process_file

CONFIG_DIR = FIXTURES.parent.parent / "config"
NIGHTS = [("OKCON", "pep_final_audit.txt"), ("TXI47", "choice_night_audit.txt"),
          ("CANDLEWOOD-MOORE", "opera_trial_balance.txt")]


@pytest.fixture()
def props():
    return load_properties(CONFIG_DIR / "properties.example.yaml")


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "portal.db")


@pytest.fixture()
def odoo(props):
    """A fake Odoo that knows the companies, journals, analytics and accounts these nights use."""
    t = FakeTransport()
    codes = set()
    for i, (code, f) in enumerate(NIGHTS, start=2):
        res = process_file(FIXTURES / f, props)
        t.records["res.company"].append({"id": i, "name": res.entry.company_code or code})
        t.records["account.journal"].append({"id": 70 + i, "code": res.entry.journal_code,
                                             "name": "NA", "company_id": i})
        t.records["account.analytic.account"].append({"id": 90 + i, "code": code, "name": code})
        codes |= {l.account_code for l in res.entry.lines}
    have = {r["code"] for r in t.records["account.account"]}
    for n, c in enumerate(sorted(codes - have), start=300):
        t.records["account.account"].append({"id": n, "code": c, "name": c, "account_type": "x"})
    return t


def load_nights(db, props, tmp_path):
    store = storage.LocalStorage(tmp_path / "files")
    import base64
    for code, f in NIGHTS:
        payload = {"From": f"audit@{code}.example", "Subject": code, "MessageID": f"<{code}>",
                   "Date": "Tue, 11 Nov 2025 02:15:00 -0600",
                   "Attachments": [{"Name": f, "Content":
                                    base64.b64encode((FIXTURES / f).read_bytes()).decode()}]}
        ingest(message_from_postmark(payload), db=db, storage=store, props=props)


def test_balanced_nights_post_themselves_as_drafts(db, props, odoo, tmp_path):
    load_nights(db, props, tmp_path)
    assert len(db.runs_awaiting_post()) == 3

    s = post_due(db, connect=lambda: OdooClient(odoo))
    assert len(s.posted) == 3 and not s.failed
    assert db.runs_awaiting_post() == []                        # the queue drains
    moves = odoo.records["account.move"]
    assert len(moves) == 3 and all(m["state"] == "draft" for m in moves)   # drafts, not posted
    for r in [db.get_run(i) for i in (1, 2, 3)]:
        assert r["posted_at"] and r["odoo_move_id"]


def test_autopost_posts_instead_of_drafting(db, props, odoo, tmp_path):
    load_nights(db, props, tmp_path)
    post_due(db, autopost=True, connect=lambda: OdooClient(odoo))
    assert all(m["state"] == "posted" for m in odoo.records["account.move"])


def test_running_again_creates_nothing(db, props, odoo, tmp_path):
    load_nights(db, props, tmp_path)
    post_due(db, connect=lambda: OdooClient(odoo))
    before = len(odoo.records["account.move"])
    again = post_due(db, connect=lambda: OdooClient(odoo))
    assert not again.did_anything and len(odoo.records["account.move"]) == before


def test_one_hotel_failing_does_not_stop_the_others(db, props, odoo, tmp_path):
    load_nights(db, props, tmp_path)
    # the Candlewood's analytic account is missing from Odoo, as it would be on a fresh chart
    odoo.records["account.analytic.account"] = [
        r for r in odoo.records["account.analytic.account"] if r["code"] != "CANDLEWOOD-MOORE"]

    s = post_due(db, connect=lambda: OdooClient(odoo))
    assert len(s.posted) == 2 and len(s.failed) == 1
    bad_id, why = s.failed[0]
    assert "CANDLEWOOD-MOORE" in why
    row = db.get_run(bad_id)
    assert row["posted_at"] is None and row["post_attempts"] == 1 and "CANDLEWOOD" in row["post_error"]
    # the two good ones are done and are not retried
    assert [r["id"] for r in db.runs_awaiting_post()] == [bad_id]

    # fix Odoo and the straggler goes on its own, with no help and no duplicate of the others
    odoo.records["account.analytic.account"].append({"id": 99, "code": "CANDLEWOOD-MOORE", "name": "x"})
    s2 = post_due(db, connect=lambda: OdooClient(odoo))
    assert len(s2.posted) == 1 and db.get_run(bad_id)["posted_at"]
    assert len(odoo.records["account.move"]) == 3


def test_a_night_that_keeps_failing_stops_retrying_and_waits_for_a_person(db, props, odoo, tmp_path):
    load_nights(db, props, tmp_path)
    odoo.records["account.analytic.account"] = []          # nothing will post
    for _ in range(MAX_ATTEMPTS):
        post_due(db, connect=lambda: OdooClient(odoo))
    assert db.runs_awaiting_post() == []                   # given up
    stuck = db.runs_stuck()
    assert len(stuck) == 3 and all(r["post_attempts"] == MAX_ATTEMPTS for r in stuck)
    assert all(r["post_error"] for r in stuck)             # and each says why


def test_odoo_being_down_is_not_held_against_any_night(db, props, odoo, tmp_path):
    load_nights(db, props, tmp_path)

    def refuse():
        raise OdooError("connection refused")

    s = post_due(db, connect=refuse)
    assert len(s.failed) == 3 and not s.posted
    # nothing was attempted, so nothing is closer to giving up
    assert all(r["post_attempts"] in (0, None) for r in db.runs_awaiting_post())
    assert len(db.runs_awaiting_post()) == 3


def test_an_unbalanced_night_is_never_queued(db, props, tmp_path):
    store = storage.LocalStorage(tmp_path / "f")
    res = process_file(FIXTURES / "generic_daily_sample.txt", props)
    db.add_run(uploaded_by="t", property_code=res.property_code or "", business_date=None,
               pms=res.pms, ref=res.ref, status=res.status, message=res.message,
               file_name="x.txt", stored_path="x", result_json=res.to_json())
    assert db.runs_awaiting_post() == []


def test_webhook_to_odoo_end_to_end(tmp_path, monkeypatch, props, odoo):
    """A mail provider POSTs a pack and a draft entry appears in Odoo, with nobody involved."""
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CONFIG_DIR / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CONFIG_DIR / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("INTAKE_TOKEN", "s3cret")
    monkeypatch.setenv("DELIVERY_MODE", "odoo")
    monkeypatch.setenv("ODOO_URL", "https://example.odoo.com")
    monkeypatch.setenv("ODOO_API_KEY", "key")
    monkeypatch.setattr(OdooClient, "connect", classmethod(lambda cls, s: OdooClient(odoo)))

    import importlib
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient

    import base64
    f = "pep_final_audit.txt"
    body = {"From": "audit@okcon.example", "Subject": "Final Audit", "MessageID": "<e2e>",
            "Date": "Tue, 11 Nov 2025 02:15:00 -0600",
            "Attachments": [{"Name": f,
                             "Content": base64.b64encode((FIXTURES / f).read_bytes()).decode()}]}
    with TestClient(app_module.app, follow_redirects=False) as client:
        r = client.post("/intake/mail?token=s3cret", json=body)
    assert r.status_code == 200 and r.json()["runs"][0]["status"] == "ok"

    moves = [m for m in odoo.records["account.move"] if m.get("ref") == "PEP-OKCON-2025-11-10"]
    assert len(moves) == 1 and moves[0]["state"] == "draft"
    assert app_module.state.db.get_run(1)["posted_at"]
