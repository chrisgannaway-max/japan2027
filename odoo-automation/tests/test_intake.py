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
