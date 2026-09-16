"""The morning list and the loop that sends it."""
import base64
from datetime import date, datetime, time
from pathlib import Path

import pytest

from conftest import FIXTURES
from fake_odoo import FakeTransport
from portal import daily, mail, scheduler, storage
from portal.db import Database
from portal.intake import ingest, message_from_postmark
from portal.poster import post_due
from pms_to_odoo.odoo_client import OdooClient
from pms_to_odoo.pipeline import load_properties

CONFIG_DIR = FIXTURES.parent.parent / "config"
DAY = date(2025, 11, 10)                       # the business date in the PEP fixture
NEXT_MORNING = datetime(2025, 11, 11, 7, 0)    # after a 06:00 cut-off


@pytest.fixture()
def props():
    return load_properties(CONFIG_DIR / "properties.example.yaml")


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "portal.db")


def deliver(db, props, tmp_path, name):
    store = storage.LocalStorage(tmp_path / "files")
    payload = {"From": "audit@example", "Subject": name, "MessageID": f"<{name}>",
               "Date": "Tue, 11 Nov 2025 02:15:00 -0600",
               "Attachments": [{"Name": name,
                                "Content": base64.b64encode((FIXTURES / name).read_bytes()).decode()}]}
    return ingest(message_from_postmark(payload), db=db, storage=store, props=props)


class FakeState:
    def __init__(self, db, props, delivery="download"):
        self.db, self.props, self.delivery = db, props, delivery
        self.odoo_enabled, self.autopost = False, False


# ------------------------------------------------------------------ the list itself
def test_nothing_arrived_is_missing_only_after_the_cut_off(db, props):
    before = daily.build(db, props, DAY, datetime(2025, 11, 11, 5, 0))
    assert {r.state for r in before.rows} == {"waiting"} and before.all_clear

    after = daily.build(db, props, DAY, NEXT_MORNING)
    assert {r.state for r in after.rows} == {"missing"}
    assert len(after.trouble) == len(props)


def test_a_property_can_have_its_own_cut_off(props):
    early = dict(props["OKCON"]) | {"due_by": "04:30"}
    assert daily.due_by(early) == time(4, 30)
    assert daily.due_by(props["OKCON"]) == daily.DEFAULT_DUE_BY     # the default otherwise
    assert daily.due_by({"due_by": "nonsense"}) == daily.DEFAULT_DUE_BY   # a typo is not fatal


def test_the_four_kinds_of_trouble_land_on_one_list(db, props, tmp_path):
    deliver(db, props, tmp_path, "pep_final_audit.txt")              # OKCON: balanced
    report = daily.build(db, props, DAY, NEXT_MORNING)
    by_code = {r.code: r for r in report.rows}
    assert by_code["OKCON"].state == "ready"                          # arrived, waiting for Odoo
    assert by_code["TXI47"].state == "missing"                        # never turned up
    assert daily.HEADINGS[by_code["TXI47"].state] == "Nothing has arrived"

    text = daily.as_text(report)
    assert "Nothing has arrived" in text and "TXI47" in text
    assert "need attention" in text


def test_a_stuck_night_is_named_with_its_reason(db, props, tmp_path):
    deliver(db, props, tmp_path, "pep_final_audit.txt")
    for _ in range(5):                                  # Odoo refuses every time
        db.record_post_failure(1, "GL account with code '4000' not found in Odoo")
    report = daily.build(db, props, DAY, NEXT_MORNING)
    row = {r.code: r for r in report.rows}["OKCON"]
    assert row.state == "stuck" and "4000" in row.detail
    assert "Odoo would not take it" in daily.as_text(report)


def test_all_clear_says_so_and_nothing_else(db, props, tmp_path):
    only = {"OKCON": props["OKCON"]}
    deliver(db, props, tmp_path, "pep_final_audit.txt")
    t = FakeTransport()
    t.records["account.analytic.account"].append({"id": 51, "code": "OKCON", "name": "x"})
    have = {r["code"] for r in t.records["account.account"]}
    from pms_to_odoo.pipeline import RunResult
    for n, l in enumerate(RunResult.from_json(db.get_run(1)["result_json"]).entry.lines, start=400):
        if l.account_code not in have:
            t.records["account.account"].append({"id": n, "code": l.account_code, "name": "x", "account_type": "x"})
            have.add(l.account_code)
    post_due(db, connect=lambda: OdooClient(t))

    report = daily.build(db, only, DAY, NEXT_MORNING)
    assert report.all_clear and report.rows[0].state == "posted"
    text = daily.as_text(report)
    assert "Nothing to do" in text and "need attention" not in text


# ------------------------------------------------------------------ the loop
def test_the_report_waits_for_the_last_cut_off_then_goes_once(db, props, monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "send_reporting", lambda to, s, b: (sent.append((to, s, b)), (True, ""))[1])
    monkeypatch.setenv("REPORT_TO", "office@champion.example")
    st = FakeState(db, props)

    early = scheduler.tick(st, datetime(2025, 11, 11, 5, 30))
    assert not early.report_sent and "before the 06:00 cut-off" in early.report_why
    assert sent == []

    on_time = scheduler.tick(st, NEXT_MORNING)
    assert on_time.report_sent and len(sent) == 1
    to, subject, body = sent[0]
    assert to == "office@champion.example" and "2025-11-10" in subject and "need attention" in subject
    assert "Nothing has arrived" in body

    again = scheduler.tick(st, datetime(2025, 11, 11, 9, 0))
    assert not again.report_sent and again.report_why == "already sent"
    assert len(sent) == 1                                   # once a day, not once a tick


def test_no_address_means_no_report_and_a_reason_not_a_crash(db, props, monkeypatch):
    monkeypatch.delenv("REPORT_TO", raising=False)
    mail.set_stored({})
    st = FakeState(db, props)
    out = scheduler.tick(st, NEXT_MORNING)
    assert not out.report_sent and "REPORT_TO" in out.report_why


def test_a_failing_pass_does_not_kill_the_loop(db, props, monkeypatch):
    monkeypatch.setenv("REPORT_TO", "x@example.com")
    monkeypatch.setattr(scheduler, "send_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = scheduler.tick(FakeState(db, props), NEXT_MORNING)
    assert not out.report_sent and "boom" in out.report_why     # reported, not raised


def test_business_date_is_the_night_before():
    assert scheduler.business_date_for(datetime(2025, 11, 11, 6, 0)) == date(2025, 11, 10)


def test_scheduler_is_off_unless_asked_for(db, props, monkeypatch):
    monkeypatch.delenv("SCHEDULER", raising=False)
    assert scheduler.start(FakeState(db, props)) is None
    monkeypatch.setenv("SCHEDULER", "on")
    monkeypatch.setenv("SCHEDULER_INTERVAL", "3600")
    t = scheduler.start(FakeState(db, props))
    assert t is not None and t.daemon                      # never keeps the process alive
