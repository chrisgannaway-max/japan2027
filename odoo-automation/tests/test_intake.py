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
