"""Shared pipeline, CSV export and the web portal (via FastAPI's test client)."""
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import FIXTURES
from pms_to_odoo.export import HEADERS, entries_to_flat_csv, entries_to_odoo_csv
from pms_to_odoo.pipeline import load_properties, match_property, process_file

CONFIG_DIR = FIXTURES.parent.parent / "config"


@pytest.fixture(scope="module")
def props():
    return load_properties(CONFIG_DIR / "properties.example.yaml")


def test_pipeline_recognises_property_from_report(props):
    res = process_file(FIXTURES / "pep_final_audit.txt", props)
    assert res.status == "ok" and res.property_code == "OKCON" and res.ref == "PEP-OKCON-2025-11-10"
    assert res.entry.imbalance == 0 and res.entry.total_debit == Decimal("92570.78")
    assert any(st == "1210" for _, _, _, st in res.coverage)
    # JSON round trip keeps the entry intact
    from pms_to_odoo.pipeline import RunResult
    back = RunResult.from_json(res.to_json())
    assert back.entry.total_debit == res.entry.total_debit and back.status == "ok" and back.ref == res.ref


def test_pipeline_manager_scope_and_unknown(props):
    res = process_file(FIXTURES / "pep_final_audit.txt", props, allowed={"TXI47"})
    assert res.status == "unknown_property"
    res = process_file(FIXTURES / "pep_final_audit.txt", props, property_code="TXI47", allowed={"TXI47"})
    assert res.status == "wrong_pms" and "CHOICEADV" in res.message      # PEP file forced onto a Choice property
    res = process_file(FIXTURES / "generic_daily_sample.txt", props)
    assert res.status == "unrecognised"


def test_pipeline_synxis_pair(props):
    res = process_file(FIXTURES / "synxis" / "transaction_totals_summary.txt", props)
    assert res.status == "ok" and res.property_code == "LQ89051" and len(res.companions) == 1


def test_match_property_rules(props):
    assert match_property(props, Path("x.pdf"), "TXI47", "", "CHOICEADV") == "TXI47"
    assert match_property(props, Path("x.pdf"), "", "Candlewood Suites Moore Oklahoma 11-10-25", "OPERA") == "CANDLEWOOD-MOORE"
    assert match_property(props, Path("OKCMD_2025-09-30.pdf"), "", "", None) == "OKCMD"
    assert match_property(props, Path("x.pdf"), "", "", "PEP") == "OKCON"           # only PEP property
    assert match_property(props, Path("x.pdf"), "", "", None, allowed={"TXI47"}) == "TXI47"   # single allowed


def test_odoo_csv_layout(props):
    res = process_file(FIXTURES / "opera_trial_balance.txt", props)
    csv_text = entries_to_odoo_csv([res.entry])
    lines = csv_text.strip().splitlines()
    assert lines[0].split(",") == HEADERS
    first = lines[1].split(",")
    assert first[:3] == ["NA", "2025-11-09", "OPERA-CANDLEWOOD-MOORE-2025-11-09"]
    assert lines[2].startswith(",,,")                     # continuation rows leave entry columns blank
    assert len(lines) - 1 == len(res.entry.lines)
    flat = entries_to_flat_csv([res.entry]).splitlines()
    assert flat[1].startswith("OPERA-CANDLEWOOD-MOORE-2025-11-09,2025-11-09,NA,")


# ------------------------------------------------------------------ portal
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CONFIG_DIR / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CONFIG_DIR / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.delenv("ODOO_URL", raising=False)
    import importlib
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    return TestClient(app_module.app, follow_redirects=False)


def login(client, user, pw):
    r = client.post("/login", data={"username": user, "password": pw})
    assert r.status_code == 303, r.text
    return r


def test_login_required_and_roles(client):
    assert client.get("/").status_code == 303
    assert client.get("/upload").status_code == 303
    login(client, "okcon", "okcon")
    assert client.get("/").status_code == 303                    # manager is sent to /upload
    assert client.get("/upload").status_code == 200
    assert client.get("/export/2025-11-10.csv").status_code == 403
    r = client.post("/login", data={"username": "okcon", "password": "wrong"})
    assert r.status_code == 200 and "Wrong username" in r.text


def test_manager_upload_admin_review_and_export(client):
    login(client, "okcon", "okcon")
    with open(FIXTURES / "pep_final_audit.txt", "rb") as fh:
        r = client.post("/upload", data={"property_code": "OKCON"}, files=[("files", ("OKCON_final_audit.txt", fh, "text/plain"))])
    assert r.status_code == 200 and "Balanced, ready to post" in r.text and "92,570.78" in r.text
    # manager cannot upload for another property
    with open(FIXTURES / "choice_night_audit.txt", "rb") as fh:
        r = client.post("/upload", data={"property_code": ""}, files=[("files", ("choice.txt", fh, "text/plain"))])
    assert "Property not recognised" in r.text
    # admin dashboard for that day
    client.get("/logout")
    login(client, "admin", "admin")
    r = client.get("/?day=2025-11-10")
    assert r.status_code == 200 and "OKCON" in r.text and "Balanced, ready to post" in r.text and "Not uploaded" in r.text
    run_id = int(r.text.split('/runs/')[1].split('"')[0])
    r = client.get(f"/runs/{run_id}")
    assert r.status_code == 200 and "Journal entry PEP-OKCON-2025-11-10" in r.text and "Approve" in r.text
    assert client.post(f"/runs/{run_id}/approve").status_code == 303
    r = client.get("/export/2025-11-10.csv?fmt=odoo&approved=yes")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    body = r.text.splitlines()
    assert body[0].split(",") == HEADERS and "PEP-OKCON-2025-11-10" in body[1]
    r = client.get(f"/runs/{run_id}")
    assert "approved by admin" in r.text
    # no Odoo configured -> posting is refused cleanly
    assert client.post(f"/runs/{run_id}/post", data={"post_now": "no"}).status_code == 400


def test_synxis_pair_upload_merges(client):
    login(client, "lq89051", "lq89051")
    files = [("files", (name, (FIXTURES / "synxis" / name).read_bytes(), "text/plain"))
             for name in ("transaction_totals_summary.txt", "hotel_ledger_compare.txt")]
    r = client.post("/upload", data={"property_code": "LQ89051"}, files=files)
    assert r.status_code == 200
    result_section = r.text.split("<h2>Result</h2>")[1].split("<h2>Recent uploads</h2>")[0]
    assert result_section.count("<tr><td>") == 1                 # the pair became one run
    assert "Balanced, ready to post" in result_section and "5,903.42" in r.text
    client.get("/logout")
    login(client, "admin", "admin")
    r = client.get("/?day=2025-11-11")
    assert "LQ89051" in r.text and "5,903.42" in r.text


def test_same_nightly_file_uploaded_twice_yields_one_entry(client):
    login(client, "okcon", "okcon")
    for i in (1, 2, 3):
        with open(FIXTURES / "pep_final_audit.txt", "rb") as fh:
            r = client.post("/upload", data={"property_code": "OKCON"},
                            files=[("files", (f"audit{i}.txt", fh, "text/plain"))])
        assert r.status_code == 200 and "Balanced, ready to post" in r.text
    import portal.app as app_module
    runs = app_module.state.db.recent_runs(10)
    assert len(runs) == 3, "each upload is kept for the audit trail"
    assert sum(1 for r in runs if not r["superseded"]) == 1, "only the newest counts"
    live = app_module.state.db.runs_for_date("2025-11-10")
    assert len(live) == 1 and live[0]["id"] == runs[0]["id"]


def test_a_day_is_not_handed_out_twice(client):
    login(client, "okcon", "okcon")
    with open(FIXTURES / "pep_final_audit.txt", "rb") as fh:
        client.post("/upload", data={"property_code": "OKCON"}, files=[("files", ("a.txt", fh, "text/plain"))])
    client.get("/logout")
    login(client, "admin", "admin")
    first = client.get("/export/2025-11-10.csv?fmt=odoo")
    assert "PEP-OKCON-2025-11-10" in first.text
    n_first = len(first.text.strip().splitlines()) - 1
    assert n_first == 43
    # a second download gets the header only: the entry is already out
    second = client.get("/export/2025-11-10.csv?fmt=odoo")
    assert "PEP-OKCON-2025-11-10" not in second.text
    assert len(second.text.strip().splitlines()) - 1 == 0
    page = client.get("/?day=2025-11-10").text
    assert "already been downloaded or posted" in page
    assert "Importing the same day twice would book it twice" in page
    # but it can be fetched again deliberately
    again = client.get("/export/2025-11-10.csv?fmt=odoo&again=yes")
    assert "PEP-OKCON-2025-11-10" in again.text
    assert len(again.text.strip().splitlines()) - 1 == 43


def test_ambiguous_property_name_is_held_not_guessed(props):
    """Two hotels at the same airport: a name that matches both must not pick one."""
    from pms_to_odoo.pipeline import match_property
    from pathlib import Path as _P
    twins = dict(props)
    twins["OKCAW2"] = dict(props["OKCAW"]) | {
        "code": "OKCAW2", "pms_property_name": "SpringHill Suites By Marriott Oklahoma City Airport"}
    report = "SpringHill Suites By Marriott Oklahoma City Airport West"
    assert match_property(twins, _P("x.pdf"), "", report, "AGILYSYS") is None   # held, not guessed
    assert match_property(props, _P("x.pdf"), "", report, "AGILYSYS") == "OKCAW"  # unambiguous still works
