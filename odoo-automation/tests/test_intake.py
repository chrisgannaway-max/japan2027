"""Night-audit packs arriving as e-mail rather than uploads."""
from email.message import EmailMessage
from pathlib import Path

import pytest

from conftest import FIXTURES
from portal import storage
from portal.db import Database
from portal.intake import FolderSource, ingest, ingest_source, message_from_bytes
from pms_to_odoo.pipeline import load_properties

CONFIG_DIR = FIXTURES.parent.parent / "config"


@pytest.fixture()
def props():
    return load_properties(CONFIG_DIR / "properties.example.yaml")


@pytest.fixture()
def bits(tmp_path):
    return Database(tmp_path / "portal.db"), storage.LocalStorage(tmp_path / "files")


def an_email(*files, sender="nightaudit@okcon.example", subject="Final Audit", msg_id="<a@b>"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "reports@example.com", subject
    m["Message-Id"] = msg_id
    m["Date"] = "Tue, 11 Nov 2025 02:15:00 -0600"
    m.set_content("Attached.")
    for f in files:
        p = Path(f)
        m.add_attachment(p.read_bytes(), maintype="application", subtype="pdf", filename=p.name)
    return m.as_bytes()


def test_reads_the_report_out_of_an_email(bits, props):
    db, store = bits
    msg = message_from_bytes(an_email(FIXTURES / "pep_final_audit.txt"))
    assert msg.sender.startswith("nightaudit@okcon") and len(msg.reports) == 1
    res = ingest(msg, db=db, storage=store, props=props)
    assert len(res.created) == 1
    run_id, out = res.created[0]
    assert out.status == "ok" and out.property_code == "OKCON"
    assert db.get_run(run_id)["uploaded_by"].startswith("email:")
    # the file itself is kept, because it is the evidence behind the entry
    assert store.local_path(db.get_run(run_id)["stored_path"]).exists()


def test_the_same_report_forwarded_again_is_not_taken_twice(bits, props):
    db, store = bits
    first = ingest(message_from_bytes(an_email(FIXTURES / "pep_final_audit.txt", msg_id="<1@x>")),
                   db=db, storage=store, props=props)
    assert len(first.created) == 1
    # forwarded by somebody else: new Message-Id, new sender, same attachment bytes
    again = ingest(message_from_bytes(an_email(FIXTURES / "pep_final_audit.txt", msg_id="<2@x>",
                                               sender="gm@okcon.example")),
                   db=db, storage=store, props=props)
    assert again.did_nothing and again.duplicates == ["pep_final_audit.txt"]
    assert len(db.recent_runs(10, None)) == 1


def test_non_reports_are_ignored_not_refused(bits, props):
    db, store = bits
    raw = an_email(FIXTURES / "pep_final_audit.txt")
    m = message_from_bytes(raw)
    m.attachments.append(type(m.attachments[0])("signature.png", b"\x89PNG not a report"))
    res = ingest(m, db=db, storage=store, props=props)
    assert len(res.created) == 1 and res.ignored == ["signature.png"]


def test_a_synxis_pair_in_one_message_is_merged(bits, props):
    db, store = bits
    pair = sorted((FIXTURES / "synxis").glob("*.txt"))
    assert len(pair) == 2
    res = ingest(message_from_bytes(an_email(*pair)), db=db, storage=store, props=props)
    assert len(res.created) == 1                      # one night, not two runs
    _, out = res.created[0]
    assert out.status == "ok" and out.property_code == "LQ89051" and len(out.companions) == 1


def test_folder_source_takes_eml_and_bare_files(tmp_path, bits, props):
    db, store = bits
    drop = tmp_path / "drop"
    drop.mkdir()
    (drop / "okcon.eml").write_bytes(an_email(FIXTURES / "pep_final_audit.txt"))
    (drop / "choice_night_audit.txt").write_bytes((FIXTURES / "choice_night_audit.txt").read_bytes())
    results = ingest_source(FolderSource(drop), db=db, storage=store, props=props)
    codes = {out.property_code for r in results for _, out in r.created}
    assert codes == {"OKCON", "TXI47"}
    # re-scanning the same folder changes nothing
    again = ingest_source(FolderSource(drop), db=db, storage=store, props=props)
    assert all(r.did_nothing for r in again) and len(db.recent_runs(10, None)) == 2


def test_an_unreadable_attachment_is_still_kept(bits, props):
    db, store = bits
    m = message_from_bytes(an_email(FIXTURES / "generic_daily_sample.txt"))
    res = ingest(m, db=db, storage=store, props=props)
    run_id, out = res.created[0]
    assert out.status in ("unrecognised", "unknown_property")
    assert store.local_path(db.get_run(run_id)["stored_path"]).exists()   # evidence, not discarded


# ------------------------------------------------------------------ the webhook
def postmark_payload(*files, sender="nightaudit@okcon.example", msg_id="abc-123"):
    """The JSON Postmark POSTs for one inbound message."""
    import base64
    return {
        "FromName": "Night Audit", "From": sender, "FromFull": {"Email": sender, "Name": "Night Audit"},
        "To": "9f2@inbound.postmarkapp.com", "OriginalRecipient": "9f2@inbound.postmarkapp.com",
        "Subject": "Final Audit", "MessageID": msg_id,
        "Date": "Tue, 11 Nov 2025 02:15:00 -0600", "TextBody": "Attached.",
        "Attachments": [{"Name": Path(f).name,
                         "Content": base64.b64encode(Path(f).read_bytes()).decode(),
                         "ContentType": "application/pdf",
                         "ContentLength": Path(f).stat().st_size} for f in files],
    }


def test_postmark_payload_becomes_a_message(bits, props):
    db, store = bits
    from portal.intake import message_from_postmark
    msg = message_from_postmark(postmark_payload(FIXTURES / "pep_final_audit.txt"))
    assert msg.sender == "nightaudit@okcon.example" and len(msg.reports) == 1
    res = ingest(msg, db=db, storage=store, props=props)
    assert len(res.created) == 1 and res.created[0][1].property_code == "OKCON"


def test_a_corrupt_attachment_does_not_lose_the_rest(props, bits):
    from portal.intake import message_from_postmark
    payload = postmark_payload(FIXTURES / "pep_final_audit.txt")
    payload["Attachments"].insert(0, {"Name": "broken.pdf", "Content": "!!!not base64!!!"})
    msg = message_from_postmark(payload)
    assert [a.name for a in msg.attachments] == ["pep_final_audit.txt"]


@pytest.fixture()
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CONFIG_DIR / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CONFIG_DIR / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("INTAKE_TOKEN", "s3cret")
    monkeypatch.delenv("ODOO_URL", raising=False)
    import importlib
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    return TestClient(app_module.app, follow_redirects=False), app_module


def test_webhook_needs_the_token(web):
    client, _ = web
    body = postmark_payload(FIXTURES / "pep_final_audit.txt")
    assert client.post("/intake/mail", json=body).status_code == 403
    assert client.post("/intake/mail?token=wrong", json=body).status_code == 403


def test_webhook_files_the_report(web):
    client, app_module = web
    body = postmark_payload(FIXTURES / "pep_final_audit.txt")
    r = client.post("/intake/mail", json=body, headers={"X-Intake-Token": "s3cret"})
    assert r.status_code == 200
    assert r.json()["runs"] == [{"id": 1, "property": "OKCON", "status": "ok"}]
    # a retry from the provider must not file it twice
    again = client.post("/intake/mail?token=s3cret", json=body)
    assert again.json()["runs"] == [] and again.json()["duplicates"] == ["pep_final_audit.txt"]
    assert len(app_module.state.db.recent_runs(10, None)) == 1


def test_webhook_is_off_without_a_token_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_SECRET", "x")
    monkeypatch.delenv("INTAKE_TOKEN", raising=False)
    import importlib
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, follow_redirects=False)
    assert c.post("/intake/mail", json={}).status_code == 404


# ------------------------------------------------------------------ SynXis, two e-mails
def synxis_email(path, msg_id):
    import base64
    return {"From": "reports@synxis.example", "Subject": Path(path).stem, "MessageID": msg_id,
            "Date": "Wed, 12 Nov 2025 17:06:00 +0000",
            "Attachments": [{"Name": Path(path).name,
                             "Content": base64.b64encode(Path(path).read_bytes()).decode()}]}


@pytest.mark.parametrize("first_half", ["transaction_totals_summary.txt", "hotel_ledger_compare.txt"])
def test_synxis_halves_in_separate_emails_are_paired(bits, props, first_half):
    """SynXis sends revenue and ledgers as two reports. On a schedule they arrive as two
    e-mails, and neither balances alone -- whichever lands second must do the joining."""
    db, store = bits
    from portal.intake import message_from_postmark
    halves = {p.name: p for p in (FIXTURES / "synxis").glob("*.txt")}
    second_half = next(n for n in halves if n != first_half)

    one = ingest(message_from_postmark(synxis_email(halves[first_half], "<a>")),
                 db=db, storage=store, props=props)
    run_id, out = one.created[0]
    assert out.status == "awaiting_companion"          # not "unbalanced": nothing is wrong yet
    assert "has not arrived yet" in out.message
    assert db.awaiting_companion("LQ89051", "2025-11-11")["id"] == run_id

    two = ingest(message_from_postmark(synxis_email(halves[second_half], "<b>")),
                 db=db, storage=store, props=props)
    _, merged = two.created[0]
    assert merged.status == "ok" and merged.property_code == "LQ89051"
    assert len(merged.companions) == 1                 # both halves read together
    # the half that was waiting is superseded, so the night appears once
    assert db.get_run(run_id)["superseded"] == 1
    live = [r for r in db.recent_runs(10, None) if not r["superseded"]]
    assert len(live) == 1 and live[0]["status"] == "ok"


def test_a_lone_synxis_half_keeps_waiting_and_says_so(bits, props):
    db, store = bits
    from portal.daily import build
    from datetime import date, datetime
    from portal.intake import message_from_postmark
    half = FIXTURES / "synxis" / "transaction_totals_summary.txt"
    ingest(message_from_postmark(synxis_email(half, "<only>")), db=db, storage=store, props=props)
    report = build(db, {"LQ89051": props["LQ89051"]}, date(2025, 11, 11), datetime(2025, 11, 12, 7, 0))
    row = report.rows[0]
    assert row.state == "half" and row.is_trouble
    assert "Hotel Ledger Comparison Report has not arrived yet" in row.detail
    from portal.daily import as_text
    assert "Only half the report arrived" in as_text(report)
