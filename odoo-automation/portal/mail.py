"""Outgoing e-mail, used for password resets (and later the missing-uploads nudge).

Configured entirely by environment variables, so nothing secret sits in the repository:

    SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    SMTP_SECURITY   starttls (default) | ssl | none
    PORTAL_BASE_URL https://nightaudit.example.com   - used to build links

If SMTP_HOST is unset, `configured()` is False and the caller tells the user to contact
their administrator rather than pretending an e-mail went out.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Optional


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def base_url() -> str:
    return os.environ.get("PORTAL_BASE_URL", "").rstrip("/")


def send(to: str, subject: str, body: str) -> bool:
    """True if it was handed to the mail server.  Never raises: a failed e-mail must not
    take the site down, and the caller shows the same message either way."""
    if not configured() or not to:
        return False
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    security = os.environ.get("SMTP_SECURITY", "starttls").lower()
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
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
        return True
    except Exception as e:  # noqa: BLE001 - log and carry on
        print(f"[mail] could not send to {to}: {type(e).__name__}: {e}")
        return False


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
