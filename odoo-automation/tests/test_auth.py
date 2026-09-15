"""Logins, throttling, password reset and two-step sign-in."""
import importlib
import re

import pyotp
import pytest

from conftest import FIXTURES
from portal.auth import LoginThrottle, hash_password, verify_password, verify_totp

CFG = FIXTURES.parent.parent / "config"


def make(tmp_path, monkeypatch, store="db", mfa_roles="admin", smtp=True):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CFG / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CFG / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("PORTAL_STORE", store)
    monkeypatch.setenv("PORTAL_MFA_ROLES", mfa_roles)
    for k, v in (("SMTP_HOST", "localhost"), ("SMTP_FROM", "portal@example.com"),
                 ("PORTAL_BASE_URL", "https://portal.example.com")):
        monkeypatch.setenv(k, v) if smtp else monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("ODOO_URL", raising=False)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    if app_module.state.store is not None:
        app_module.state.store.import_from_yaml(app_module.CONFIG, app_module.USERS)
        app_module.state.reload_config()
    return TestClient(app_module.app, follow_redirects=False), app_module


# ------------------------------------------------------------------ basics
def test_passwords_are_hashed_not_stored():
    h = hash_password("correct horse battery staple")
    assert h.startswith("pbkdf2$200000$") and "correct horse" not in h
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)
    assert hash_password("x") != hash_password("x"), "each hash gets its own salt"


def test_throttle_locks_then_frees():
    t = LoginThrottle(max_failures=3, per_ip=100, window=900, lockout=900)
    for _ in range(2):
        t.record_failure("10.0.0.1", "gm")
    assert t.locked_for("10.0.0.1", "gm") == 0
    t.record_failure("10.0.0.1", "gm")
    assert t.locked_for("10.0.0.1", "gm") > 0
    assert t.locked_for("10.0.0.2", "gm") == 0, "another address is unaffected"
    assert t.locked_for("10.0.0.1", "other") == 0, "another username is unaffected"


def test_throttle_also_catches_spraying_many_usernames():
    t = LoginThrottle(max_failures=5, per_ip=3, window=900, lockout=900)
    for name in ("a", "b", "c"):
        t.record_failure("10.0.0.9", name)
    assert t.locked_for("10.0.0.9", "never-tried") > 0


# ------------------------------------------------------------------ login
def test_repeated_wrong_passwords_are_throttled(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch, mfa_roles="")
    for _ in range(5):
        r = c.post("/login", data={"username": "okcon", "password": "nope"})
        assert "Wrong username or password" in r.text
    r = c.post("/login", data={"username": "okcon", "password": "nope"})
    assert "Too many attempts" in r.text
    # even the right password is refused while locked out
    assert "Too many attempts" in c.post("/login", data={"username": "okcon", "password": "okcon"}).text


def test_session_cookie_is_secure_behind_https(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, mfa_roles="")
    plain = c.post("/login", data={"username": "okcon", "password": "okcon"})
    assert "secure" not in plain.headers["set-cookie"].lower(), "plain http must still work locally"
    c.get("/logout")
    behind_proxy = c.post("/login", data={"username": "okcon", "password": "okcon"},
                          headers={"X-Forwarded-Proto": "https"})
    cookie = behind_proxy.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


# ------------------------------------------------------------------ password reset
def test_forgot_password_end_to_end(tmp_path, monkeypatch):
    sent = {}
    c, app_module = make(tmp_path, monkeypatch, mfa_roles="")
    monkeypatch.setattr(app_module.mail, "send", lambda to, subj, body: sent.update(to=to, body=body) or True)
    app_module.state.store.save_user("okcon", "manager", ["OKCON"], email="gm@example.com")
    app_module.state.reload_config()

    r = c.post("/forgot", data={"who": "gm@example.com"})
    assert "reset link is on its way" in r.text
    link = re.search(r"https://portal\.example\.com/reset\?token=(\S+)", sent["body"])
    assert link and sent["to"] == "gm@example.com"
    token = link.group(1)

    assert "Choose a new password" in c.get(f"/reset?token={token}").text
    assert "at least 10" in c.post("/reset", data={"token": token, "password": "short", "confirm": "short"}).text
    assert "do not match" in c.post("/reset", data={"token": token, "password": "a-long-password", "confirm": "other"}).text
    ok = c.post("/reset", data={"token": token, "password": "a-long-password", "confirm": "a-long-password"})
    assert "has been changed" in ok.text
    # the new password works, the old one does not, and the link cannot be used twice
    assert c.post("/login", data={"username": "okcon", "password": "a-long-password"}).status_code == 303
    c.get("/logout")
    assert c.post("/login", data={"username": "okcon", "password": "okcon"}).status_code == 200
    assert "expired or has already been used" in c.post(
        "/reset", data={"token": token, "password": "another-long-one", "confirm": "another-long-one"}).text


def test_forgot_password_says_the_same_thing_for_unknown_accounts(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch, mfa_roles="")
    calls = []
    monkeypatch.setattr(app_module.mail, "send", lambda *a, **k: calls.append(a) or True)
    known = c.post("/forgot", data={"who": "okcon"}).text
    unknown = c.post("/forgot", data={"who": "does-not-exist"}).text
    assert "reset link is on its way" in known and "reset link is on its way" in unknown
    assert calls == [], "no e-mail goes out when there is no address on file"


def test_forgot_is_disabled_without_smtp(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, mfa_roles="", smtp=False)
    assert "not switched on for this installation" in c.get("/forgot").text


# ------------------------------------------------------------------ MFA
def test_admin_must_set_up_mfa_then_uses_it(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch, mfa_roles="admin")
    r = c.post("/login", data={"username": "admin", "password": "admin"})
    assert r.status_code == 303
    # an admin without MFA is pushed to set it up and cannot reach anything else
    assert c.get("/").headers["location"] == "/account?setup=1"
    assert c.get("/missing").headers["location"] == "/account?setup=1"
    page = c.get("/account?setup=1")
    assert "Your role needs two-step sign-in" in page.text and "<svg" in page.text
    secret = re.search(r"<code>([A-Z2-7]{16,})</code>", page.text).group(1)
    assert "not right" in c.post("/account/mfa/enable", data={"secret": secret, "code": "000000"}).text
    good = pyotp.TOTP(secret).now()
    assert c.post("/account/mfa/enable", data={"secret": secret, "code": good}).status_code == 303
    # now the whole site is reachable again
    assert c.get("/missing").status_code == 200
    # and signing in asks for a code
    c.get("/logout")
    r = c.post("/login", data={"username": "admin", "password": "admin"})
    assert r.status_code == 200 and "Enter your code" in r.text
    pending = re.search(r'name="pending" value="([^"]+)"', r.text).group(1)
    assert "not right" in c.post("/login/mfa", data={"pending": pending, "code": "000000"}).text
    ok = c.post("/login/mfa", data={"pending": pending, "code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 303 and "session=" in ok.headers["set-cookie"]
    assert c.get("/").status_code == 200


def test_manager_mfa_is_optional_and_removable(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch, mfa_roles="admin")
    c.post("/login", data={"username": "okcon", "password": "okcon"})
    assert c.get("/upload").status_code == 200, "a manager is not forced into MFA"
    page = c.get("/account").text
    secret = re.search(r"<code>([A-Z2-7]{16,})</code>", page).group(1)
    assert c.post("/account/mfa/enable", data={"secret": secret, "code": pyotp.TOTP(secret).now()}).status_code == 303
    assert "Turn it off" in c.get("/account").text
    assert c.post("/account/mfa/disable").status_code == 303
    assert app_module.state.users.get("okcon").needs_mfa() is False


def test_admin_can_clear_mfa_for_a_lost_phone(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch, mfa_roles="")
    c.post("/login", data={"username": "okcon", "password": "okcon"})
    page = c.get("/account").text
    secret = re.search(r"<code>([A-Z2-7]{16,})</code>", page).group(1)
    c.post("/account/mfa/enable", data={"secret": secret, "code": pyotp.TOTP(secret).now()})
    assert app_module.state.users.get("okcon").needs_mfa()
    c.get("/logout")
    c.post("/login", data={"username": "admin", "password": "admin"})
    assert c.post("/admin/users/okcon/mfa-reset").status_code == 303
    assert app_module.state.users.get("okcon").needs_mfa() is False


def test_totp_tolerates_a_slightly_wrong_clock():
    secret = pyotp.random_base32()
    t = pyotp.TOTP(secret)
    import time as _t
    assert verify_totp(secret, t.at(_t.time() - 30)), "the previous code still works"
    assert not verify_totp(secret, t.at(_t.time() - 300)), "an old one does not"


# ------------------------------------------------------------------ e-mail setup page
def test_email_settings_page_and_test_button(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch, mfa_roles="", smtp=False)
    from portal import mail
    c.post("/login", data={"username": "admin", "password": "admin"})
    page = c.get("/admin/email").text
    assert "Not set up" in page and "SMTP_HOST" in page
    # saving from the browser configures it
    assert c.post("/admin/email", data={"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "portal@example.com",
                                        "SMTP_PORT": "587", "SMTP_PASSWORD": "sekret",
                                        "PORTAL_BASE_URL": "https://portal.example.com"}).status_code == 303
    assert mail.configured() and mail.setting("SMTP_PASSWORD") == "sekret"
    page = c.get("/admin/email").text
    assert "Ready" in page
    assert "sekret" not in page, "the password must never be rendered back"
    # a blank password field leaves the saved one alone
    c.post("/admin/email", data={"SMTP_HOST": "smtp2.example.com", "SMTP_FROM": "portal@example.com",
                                 "SMTP_PASSWORD": ""})
    assert mail.setting("SMTP_PASSWORD") == "sekret" and mail.setting("SMTP_HOST") == "smtp2.example.com"
    # and can be cleared deliberately
    c.post("/admin/email", data={"SMTP_HOST": "smtp2.example.com", "SMTP_FROM": "portal@example.com",
                                 "SMTP_PASSWORD": "", "clear_password": "1"})
    assert mail.setting("SMTP_PASSWORD") == ""
    # the test button reports the mail server's own reason when it fails
    r = c.post("/admin/email/test", data={"to": "someone@example.com"})
    assert r.status_code == 303 and "Could+not+send" in r.headers["location"]
    sent = {}
    monkeypatch.setattr(mail, "send_reporting", lambda to, s_, b: sent.update(to=to) or (True, ""))
    r = c.post("/admin/email/test", data={"to": "someone@example.com"})
    assert sent["to"] == "someone@example.com" and "Test+message+sent" in r.headers["location"]


def test_host_environment_wins_over_the_page(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch, mfa_roles="", smtp=True)   # SMTP_HOST set on the host
    from portal import mail
    c.post("/login", data={"username": "admin", "password": "admin"})
    page = c.get("/admin/email").text
    assert "set on the host" in page
    c.post("/admin/email", data={"SMTP_HOST": "attacker.example.com", "SMTP_FROM": "x@example.com"})
    assert mail.setting("SMTP_HOST") == "localhost", "a pinned value cannot be changed from the browser"


def test_email_page_is_admin_only(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, mfa_roles="")
    c.post("/login", data={"username": "okcon", "password": "okcon"})
    assert c.get("/admin/email").status_code == 403
    assert c.post("/admin/email/test", data={"to": "x@y.com"}).status_code == 403
