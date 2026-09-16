"""The storage adapter, against a real HTTP object store (tests/fake_supabase.py).

The point of these tests is that nothing is mocked out: the adapter makes genuine HTTP
requests, and the portal round trip below uploads a night-audit pack, parses it and reads it
back with no local file ever written by the route itself.
"""
import importlib

import pytest

from conftest import FIXTURES
from fake_supabase import TOKEN, FakeSupabase
from portal import storage

CONFIG_DIR = FIXTURES.parent.parent / "config"


@pytest.fixture()
def fake():
    server = FakeSupabase()
    yield server
    server.stop()


@pytest.fixture()
def remote(fake):
    s = storage.SupabaseStorage(fake.url, TOKEN, "night-audit")
    yield s
    s.close()


def test_local_storage_round_trip(tmp_path):
    s = storage.LocalStorage(tmp_path)
    locator = s.save("uploads/OKCON/20251110-020000/audit.txt", b"hello")
    assert locator == str(tmp_path / "uploads" / "OKCON" / "20251110-020000" / "audit.txt")
    assert s.read(locator) == b"hello" and s.exists(locator)
    assert s.local_path(locator).read_bytes() == b"hello"
    s.delete(locator)
    assert not s.exists(locator)


def test_build_picks_backend(tmp_path, monkeypatch):
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(storage.build(tmp_path), storage.LocalStorage)
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    assert isinstance(storage.build(tmp_path), storage.LocalStorage)      # two of three is not enough
    monkeypatch.setenv("SUPABASE_BUCKET", "night-audit")
    built = storage.build(tmp_path)
    assert isinstance(built, storage.SupabaseStorage) and "night-audit" in built.describe()


def test_supabase_round_trip(fake, remote):
    locator = remote.save("uploads/OKCON/20251110-020000/audit.txt", b"hello")
    assert locator == "supabase://night-audit/uploads/OKCON/20251110-020000/audit.txt"
    assert fake.objects["night-audit/uploads/OKCON/20251110-020000/audit.txt"] == b"hello"
    assert remote.read(locator) == b"hello"
    remote.save("uploads/OKCON/20251110-020000/audit.txt", b"second try")   # x-upsert replaces
    assert remote.read(locator) == b"second try"
    remote.delete(locator)
    assert not fake.objects and not remote.exists(locator)


def test_supabase_download_and_cache(fake, remote):
    fake.objects["night-audit/uploads/x.txt"] = b"put there by someone else"
    locator = "supabase://night-audit/uploads/x.txt"
    path = remote.local_path(locator)
    assert path.read_bytes() == b"put there by someone else"
    before = len([r for r in fake.requests if r[0] == "GET"])
    assert remote.local_path(locator) == path                      # served from the cache
    assert len([r for r in fake.requests if r[0] == "GET"]) == before


def test_supabase_reports_problems(fake, remote):
    with pytest.raises(FileNotFoundError):
        remote.local_path("supabase://night-audit/uploads/missing.txt")
    bad = storage.SupabaseStorage(fake.url, "wrong-key", "night-audit")
    with pytest.raises(RuntimeError) as e:
        bad.save("uploads/a.txt", b"x")
    assert "401" in str(e.value)
    bad.close()


def test_local_paths_still_work_after_the_move(tmp_path, remote):
    """Rows written before Supabase was turned on hold a plain path; they must still open."""
    legacy = tmp_path / "old.txt"
    legacy.write_text("booked last month")
    assert remote.local_path(str(legacy)).read_text() == "booked last month"
    assert remote.read(str(legacy)) == b"booked last month"


# ------------------------------------------------------------------ the portal on Supabase
@pytest.fixture()
def client(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CONFIG_DIR / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CONFIG_DIR / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("INVOICES", "on")        # this suite is about the invoice side
    monkeypatch.setenv("SUPABASE_URL", fake.url)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", TOKEN)
    monkeypatch.setenv("SUPABASE_BUCKET", "night-audit")
    monkeypatch.delenv("ODOO_URL", raising=False)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    yield TestClient(app_module.app, follow_redirects=False)
    app_module.state.storage.close()


def login(client, user, pw):
    r = client.post("/login", data={"username": user, "password": pw})
    assert r.status_code == 303, r.text


def test_upload_lands_in_the_bucket_and_reprocesses(client, fake, tmp_path):
    login(client, "okcon", "okcon")
    with open(FIXTURES / "pep_final_audit.txt", "rb") as fh:
        r = client.post("/upload", data={"property_code": "OKCON"},
                        files=[("files", ("OKCON_final_audit.txt", fh, "text/plain"))])
    assert r.status_code == 200 and "Balanced, ready to post" in r.text and "92,570.78" in r.text
    keys = list(fake.objects)
    assert len(keys) == 1 and keys[0].startswith("night-audit/uploads/OKCON/")
    assert not (tmp_path / "data" / "uploads").exists()      # nothing left in the data directory

    # the stored locator is the supabase one, and reprocessing reads it back over HTTP
    login(client, "admin", "admin")
    import portal.app as app_module
    run_id = 1
    run = app_module.state.db.get_run(run_id)
    assert run["stored_path"].startswith("supabase://night-audit/uploads/OKCON/")
    app_module.state.storage._cache.clear()               # force a real download
    r = client.post(f"/runs/{run_id}/reprocess")
    assert r.status_code == 303
    again = app_module.state.db.get_run(2)
    assert again["status"] == "ok" and again["stored_path"] == run["stored_path"]


def test_invoice_upload_and_refusal_use_the_bucket(client, fake):
    login(client, "okcon", "okcon")
    with open(FIXTURES / "invoices" / "acme_linen_INV-1001.txt", "rb") as fh:
        r = client.post("/invoices/upload", data={"property_code": "OKCON"},
                        files=[("file", ("invoice.txt", fh, "text/plain"))])
    assert r.status_code == 303
    assert any(k.startswith("night-audit/invoices/OKCON/") for k in fake.objects)

    # a night-audit pack sent to the invoice page is refused and not left in the bucket
    before = set(fake.objects)
    with open(FIXTURES / "pep_final_audit.txt", "rb") as fh:
        r = client.post("/invoices/upload", data={"property_code": "OKCON"},
                        files=[("file", ("pep.txt", fh, "text/plain"))])
    assert r.status_code == 200 and "night-audit report, not an invoice" in r.text
    assert set(fake.objects) == before


def test_new_style_secret_keys_authenticate(fake):
    """Supabase's newer sb_secret_ keys are not JWTs and are refused in Authorization: Bearer.
    They go on apikey, which is what the gateway reads for either generation of key."""
    s = storage.SupabaseStorage(fake.url, "sb_secret_abc123", "night-audit")
    # the fake only knows TOKEN, so point it at this key for the check
    import fake_supabase
    monkey = fake_supabase.TOKEN
    fake_supabase.TOKEN = "sb_secret_abc123"
    try:
        locator = s.save("uploads/x.txt", b"hello")
        assert s.read(locator) == b"hello"
        sent = fake.auth_headers[-1]
        assert sent["apikey"] == "sb_secret_abc123"
        assert "Authorization" not in sent          # not a JWT: never sent as a bearer token
    finally:
        fake_supabase.TOKEN = monkey
        s.close()


def test_legacy_jwt_keys_still_send_both_headers(fake, remote):
    remote.save("uploads/y.txt", b"hi")
    sent = fake.auth_headers[-1]
    assert sent["apikey"] == TOKEN and sent["Authorization"] == f"Bearer {TOKEN}"
