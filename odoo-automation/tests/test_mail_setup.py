"""Getting mail working, which is where a self-serve setup usually stalls.

SMTP says no in a handful of ways and only one of them reads like what it is, so the value
here is in the words that come back, not in the sending.
"""
import importlib
import smtplib
import socket
import ssl
from urllib.parse import unquote_plus

import pytest

from conftest import FIXTURES
from portal import mail

CONFIG_DIR = FIXTURES.parent.parent / "config"


@pytest.fixture()
def clean(monkeypatch):
    for k in mail.KEYS:
        monkeypatch.delenv(k, raising=False)
    mail.set_stored({})
    yield mail
    mail.set_stored({})


def test_the_scheduling_time_is_not_on_the_mail_form(clean):
    """REPORT_AT can be pinned on the host like the rest, but it is a scheduling question."""
    assert "REPORT_AT" in mail.KEYS and "REPORT_AT" not in mail.FORM_KEYS
    assert set(mail.HELP) == set(mail.FORM_KEYS)


def test_checks_name_what_is_missing(clean):
    said = dict((s, lvl) for lvl, s in mail.checks())
    assert any("No SMTP_HOST" in s for s in said) and any("No SMTP_FROM" in s for s in said)
    assert all(lvl == "bad" for s, lvl in said.items() if "SMTP_HOST" in s)

    mail.set_stored({"SMTP_HOST": "smtp.resend.com", "SMTP_FROM": "portal@example.com",
                     "PORTAL_BASE_URL": "https://x.example.com", "REPORT_TO": "office@example.com"})
    assert mail.checks() == []


def test_checks_catch_the_port_and_security_mismatch(clean):
    base = {"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "p@example.com",
            "PORTAL_BASE_URL": "https://x", "REPORT_TO": "o@example.com"}
    mail.set_stored(base | {"SMTP_PORT": "465", "SMTP_SECURITY": "starttls"})
    assert any("465 wants" in s for _, s in mail.checks())
    mail.set_stored(base | {"SMTP_PORT": "587", "SMTP_SECURITY": "ssl"})
    assert any("587 wants" in s for _, s in mail.checks())
    mail.set_stored(base | {"SMTP_PORT": "465", "SMTP_SECURITY": "ssl"})
    assert mail.checks() == []


def test_a_from_address_that_is_not_one(clean):
    mail.set_stored({"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "Night Audit",
                     "PORTAL_BASE_URL": "https://x", "REPORT_TO": "o@example.com"})
    assert any(lvl == "bad" and "not an e-mail address" in s for lvl, s in mail.checks())


def test_half_a_login_is_worth_saying(clean):
    base = {"SMTP_HOST": "h", "SMTP_FROM": "p@example.com",
            "PORTAL_BASE_URL": "https://x", "REPORT_TO": "o@example.com"}
    mail.set_stored(base | {"SMTP_USER": "resend"})
    assert any("no SMTP_PASSWORD" in s for _, s in mail.checks())
    mail.set_stored(base | {"SMTP_PASSWORD": "k"})
    assert any("no SMTP_USER" in s for _, s in mail.checks())


@pytest.mark.parametrize("error, expected", [
    (smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed"), "API key"),
    (smtplib.SMTPSenderRefused(550, b"not verified", "p@example.com"), "verified"),
    (smtplib.SMTPRecipientsRefused({"a@b.c": (550, b"no")}), "addresses you have verified"),
    (smtplib.SMTPNotSupportedError("STARTTLS extension not supported"), "SMTP_SECURITY=ssl"),
    (smtplib.SMTPServerDisconnected("closed"), "port and the security setting disagreeing"),
    (ssl.SSLError("wrong version number"), "port and the security setting disagreeing"),
    (socket.gaierror(-2, "Name or service not known"), "no such server"),
    (ConnectionRefusedError(111, "Connection refused"), "Nothing answered"),
    (TimeoutError("timed out"), "Nothing answered"),
])
def test_every_real_smtp_refusal_gets_a_sentence(error, expected):
    out = mail.explain(error, host="smtp.example.com", port=465, security="starttls")
    assert expected in out
    assert type(error).__name__ in out, "the server's own words stay, the sentence is added"


def test_an_unknown_failure_is_passed_through_rather_than_guessed_at():
    out = mail.explain(RuntimeError("something new"))
    assert out == "RuntimeError: something new"


def test_presets_are_complete_and_plausible():
    for key, p in mail.PRESETS.items():
        assert p["label"] and p["note"], key
        assert p["SMTP_HOST"] and "." in p["SMTP_HOST"], key
        # Every preset must agree with itself, or it would trip the check it exists to avoid.
        assert (p["SMTP_PORT"], p["SMTP_SECURITY"]) in (
            ("587", "starttls"), ("2525", "starttls"), ("465", "ssl")), key


# ------------------------------------------------------------------ the page itself
def make(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CONFIG_DIR / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CONFIG_DIR / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("PORTAL_STORE", "db")
    for k in list(mail.KEYS) + ["ODOO_URL", "ODOO_API_KEY", "ODOO_TRANSPORT"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, follow_redirects=False)
    assert c.post("/login", data={"username": "admin", "password": "admin"}).status_code == 303
    return c, app_module


def test_the_page_lists_what_is_missing_before_anything_is_sent(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch)
    body = c.get("/admin/email").text
    assert "Not set up" in body
    assert "No SMTP_HOST" in body and "No REPORT_TO" in body
    assert "REPORT_AT" not in body                       # that lives on the Settings page
    assert "The mail service&#39;s server name" in body or "server name" in body   # the help text


def test_choosing_a_service_fills_in_the_settings_nobody_chooses(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch)
    r = c.post("/admin/email/preset", data={"provider": "resend"})
    assert r.status_code == 303 and "Resend" in unquote_plus(r.headers["location"])
    assert mail.setting("SMTP_HOST") == "smtp.resend.com"
    assert mail.setting("SMTP_PORT") == "587" and mail.setting("SMTP_SECURITY") == "starttls"
    assert mail.setting("SMTP_USER") == "resend"
    assert mail.setting("SMTP_FROM") == "" and mail.setting("SMTP_PASSWORD") == ""   # yours to add

    # following the redirect shows the one thing the preset cannot fill in for you
    body = c.get(r.headers["location"]).text
    assert "smtp.resend.com" in body and "No SMTP_FROM" in body
    assert "resend.com/api-keys" in body

    assert "Unknown+service" in c.post("/admin/email/preset",
                                       data={"provider": "carrier pigeon"}).headers["location"]


def test_a_preset_does_not_overwrite_what_is_pinned_on_the_host(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, SMTP_HOST="smtp.pinned.example.com")
    c.post("/admin/email/preset", data={"provider": "brevo"})
    assert mail.setting("SMTP_HOST") == "smtp.pinned.example.com"
    assert mail.setting("SMTP_PORT") == "587"            # the rest still filled in


# --------------------------------------------------- against an actual mail server
class Catcher:
    """Collects what arrives, so the sending path is exercised for real and not stubbed."""

    def __init__(self):
        self.messages: list[tuple[str, list[str], str]] = []

    async def handle_DATA(self, server, session, envelope):   # noqa: N802 - aiosmtpd's name
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos),
                              envelope.content.decode("utf-8", "replace")))
        return "250 OK"


@pytest.fixture()
def catcher(clean):
    from aiosmtpd.controller import Controller
    with socket.socket() as probe:          # a port nobody else has, chosen before we bind
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    c = Catcher()
    controller = Controller(c, hostname="127.0.0.1", port=port)
    controller.start()
    mail.set_stored({"SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(port),
                     "SMTP_SECURITY": "none", "SMTP_FROM": "night-audit@example.com",
                     "PORTAL_BASE_URL": "https://portal.example.com",
                     "REPORT_TO": "office@champion.example"})
    yield c
    controller.stop()


def test_a_message_really_goes_over_smtp(catcher):
    sent, why = mail.send_reporting("office@champion.example", "Night audit 2025-11-10",
                                    "Nothing has arrived: OKCON\n")
    assert sent and why == ""
    (sender, rcpts, raw), = catcher.messages
    assert sender == "night-audit@example.com" and rcpts == ["office@champion.example"]
    assert "Subject: Night audit 2025-11-10" in raw and "Nothing has arrived: OKCON" in raw


def test_the_morning_list_can_go_to_several_people(catcher):
    """REPORT_TO takes a comma-separated list, which is how an office of three reads it."""
    to = "office@champion.example, harshil@champion.example"
    sent, why = mail.send_reporting(to, "Night audit", "body\n")
    assert sent, why
    _, rcpts, _ = catcher.messages[0]
    assert rcpts == ["office@champion.example", "harshil@champion.example"]


def test_a_reply_to_is_carried_when_set(catcher):
    mail.set_stored(dict(mail._stored) | {"SMTP_REPLY_TO": "chris@example.com"})
    assert mail.send_reporting("office@champion.example", "s", "b")[0]
    assert "Reply-To: chris@example.com" in catcher.messages[0][2]


def test_sending_to_nobody_is_refused_before_the_connection(catcher):
    sent, why = mail.send_reporting("", "s", "b")
    assert not sent and "No address" in why and catcher.messages == []


def test_a_blocked_port_is_not_reported_as_the_wrong_port():
    """Render drops outgoing 587 rather than refusing it, so a blocked host reads as a timeout.
    The old wording answered that with "587 is the one to try" -- to somebody already on 587."""
    out = mail.explain(TimeoutError("timed out"), host="smtp.postmarkapp.com", port=587,
                       security="starttls")
    assert "2525" in out
    assert "587 is the one to try" not in out


def test_being_blocked_on_2525_too_says_no_port_will_help():
    out = mail.explain(TimeoutError("timed out"), host="smtp.postmarkapp.com", port=2525,
                       security="starttls")
    assert "no port will help" in out and "HTTP API" in out


def test_the_postmark_preset_uses_a_port_that_hosts_do_not_block():
    """The presets exist to stop somebody picking a number that cannot work."""
    assert mail.PRESETS["postmark"]["SMTP_PORT"] == "2525"
    assert "587" in mail.PRESETS["postmark"]["note"]
