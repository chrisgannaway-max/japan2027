"""Outgoing e-mail, used for password resets (and later the missing-uploads nudge).

Configured entirely by environment variables, so nothing secret sits in the repository:

    SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_REPLY_TO
    SMTP_SECURITY   starttls (default) | ssl | none
    PORTAL_BASE_URL https://nightaudit.example.com   - used to build links

The same keys can be saved on the Settings page instead, which is easier for whoever runs
this day to day.  The environment always wins, so a value pinned on the host cannot be
changed from the browser.

If no host is configured, `configured()` is False and the caller tells the user to contact
their administrator rather than pretending an e-mail went out.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Optional


#: REPORT_TO and REPORT_AT are not about sending mail, but they are settings somebody changes
#: from a page and may want pinned on the host, which is exactly what this mechanism is for.
KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_SECURITY",
        "SMTP_REPLY_TO", "PORTAL_BASE_URL", "REPORT_TO", "REPORT_AT")
SECRET_KEYS = ("SMTP_PASSWORD",)

#: filled in from the database by the portal; the environment always wins over it, so a
#: value set on the host cannot be silently overridden from the browser.
_stored: dict[str, str] = {}


def set_stored(values: dict[str, str]) -> None:
    global _stored
    _stored = {k: v for k, v in (values or {}).items() if k in KEYS and v}


def setting(key: str, default: str = "") -> str:
    return os.environ.get(key) or _stored.get(key) or default


def source_of(key: str) -> str:
    if os.environ.get(key):
        return "environment"
    if _stored.get(key):
        return "saved here"
    return ""


def configured() -> bool:
    return bool(setting("SMTP_HOST") and setting("SMTP_FROM"))


def base_url() -> str:
    return setting("PORTAL_BASE_URL").rstrip("/")


def send(to: str, subject: str, body: str) -> bool:
    """True if the mail server accepted it."""
    return send_reporting(to, subject, body)[0]


def send_reporting(to: str, subject: str, body: str) -> tuple[bool, str]:
    """(sent, why not).  Never raises: a mail server having a bad day must not take the
    site down, and the reason is worth showing on the test page."""
    if not configured():
        return False, "No mail server configured (SMTP_HOST and SMTP_FROM)."
    if not to:
        return False, "No address to send to."
    msg = EmailMessage()
    msg["From"] = setting("SMTP_FROM")
    msg["To"] = to
    if setting("SMTP_REPLY_TO"):
        msg["Reply-To"] = setting("SMTP_REPLY_TO")
    msg["Subject"] = subject
    msg.set_content(body)
    host, port = setting("SMTP_HOST"), int(setting("SMTP_PORT", "587") or 587)
    security = setting("SMTP_SECURITY", "starttls").lower()
    user, password = setting("SMTP_USER"), setting("SMTP_PASSWORD")
    try:
        if security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if security == "starttls":
                server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        return True, ""
    except Exception as e:  # noqa: BLE001 - log and carry on
        print(f"[mail] could not send to {to}: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {e}"


def reset_email(username: str, link: str, minutes: int) -> tuple[str, str]:
    return ("Reset your Night Audit password",
            f"Hello {username},\n\n"
            f"Someone asked to reset the password for your Night Audit account.\n"
            f"Open this link within {minutes} minutes to choose a new one:\n\n    {link}\n\n"
            f"If that was not you, ignore this message. Your current password still works.\n")


def missing_uploads_email(day: str, missing: list[str], problems: list[str], link: Optional[str] = None) -> tuple[str, str]:
    lines = [f"Night audit for {day}", ""]
    lines += [f"Not uploaded ({len(missing)}): " + (", ".join(missing) or "none")]
    lines += [f"Uploaded but stuck ({len(problems)}): " + (", ".join(problems) or "none")]
    if link:
        lines += ["", link]
    return (f"Night audit {day}: {len(missing)} missing, {len(problems)} stuck", "\n".join(lines) + "\n")
