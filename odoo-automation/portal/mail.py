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
import socket
import ssl
from email.message import EmailMessage
from typing import Optional


#: REPORT_TO and REPORT_AT are not about sending mail, but they are settings somebody changes
#: from a page and may want pinned on the host, which is exactly what this mechanism is for.
KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_SECURITY",
        "SMTP_REPLY_TO", "PORTAL_BASE_URL", "REPORT_TO", "REPORT_AT")
SECRET_KEYS = ("SMTP_PASSWORD",)

#: what the E-mail setup page offers.  REPORT_AT is in KEYS so it can be pinned on the host
#: like the rest, but it is a scheduling question and belongs on the Settings page.
FORM_KEYS = tuple(k for k in KEYS if k != "REPORT_AT")

#: one line each, because "SMTP_FROM" tells somebody filling this in nothing at all.
HELP = {
    "SMTP_HOST": "The mail service's server name, e.g. smtp.resend.com.",
    "SMTP_PORT": "587 with starttls, or 465 with ssl. Most services want 587.",
    "SMTP_USER": "The login the service gives you. Often a fixed word like 'resend' or 'apikey'.",
    "SMTP_PASSWORD": "The API key or password. Kept on the server and never shown again.",
    "SMTP_FROM": "Who the mail comes from. It has to be an address the service has verified.",
    "SMTP_SECURITY": "starttls (port 587), ssl (port 465), or none.",
    "SMTP_REPLY_TO": "Optional. Where a reply should go, if not the From address.",
    "PORTAL_BASE_URL": "The public address of this site. Used to build the links in e-mail.",
    "REPORT_TO": "Where the morning list goes. Several addresses, separated by commas, is fine.",
}

#: The settings that are the same for everybody on a given service.  Filling these in by hand
#: is where a setup goes wrong -- 465 with starttls, or 587 with ssl, fails in a way that reads
#: like a password problem.
PRESETS = {
    "resend": {"label": "Resend", "SMTP_HOST": "smtp.resend.com", "SMTP_PORT": "587",
               "SMTP_SECURITY": "starttls", "SMTP_USER": "resend",
               "note": "The password is an API key from resend.com/api-keys. 3,000 a month free."},
    "postmark": {"label": "Postmark", "SMTP_HOST": "smtp.postmarkapp.com", "SMTP_PORT": "2525",
                 "SMTP_SECURITY": "starttls", "SMTP_USER": "",
                 "note": "User and password are both the Server API token. 100 a month free, "
                         "and the same account can receive the night-audit packs. Port 2525 "
                         "rather than the usual 587: Render blocks 587 outright, and Postmark "
                         "answers on both."},
    "brevo": {"label": "Brevo", "SMTP_HOST": "smtp-relay.brevo.com", "SMTP_PORT": "587",
              "SMTP_SECURITY": "starttls", "SMTP_USER": "",
              "note": "User is the login shown under SMTP & API; the password is the SMTP key. "
                      "300 a day free."},
    "sendgrid": {"label": "SendGrid", "SMTP_HOST": "smtp.sendgrid.net", "SMTP_PORT": "587",
                 "SMTP_SECURITY": "starttls", "SMTP_USER": "apikey",
                 "note": "The user is the literal word 'apikey'; the password is the key itself."},
    "ses": {"label": "Amazon SES", "SMTP_HOST": "email-smtp.us-east-1.amazonaws.com",
            "SMTP_PORT": "587", "SMTP_SECURITY": "starttls", "SMTP_USER": "",
            "note": "Use the region you created the identity in. SMTP credentials are not your "
                    "AWS keys -- generate them under SES > SMTP settings."},
    "gmail": {"label": "Gmail / Workspace", "SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587",
              "SMTP_SECURITY": "starttls", "SMTP_USER": "",
              "note": "User is the full address and the password is an App Password, not the "
                      "account password. Fine for testing; a mailbox is not built for this."},
}

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
        return False, explain(e, host=host, port=port, security=security)


def explain(e: Exception, host: str = "", port: int = 0, security: str = "") -> str:
    """The mail server's refusal, in words somebody can act on.

    SMTP says no in a handful of ways and only one of them reads like what it is.  A wrong
    port answers with a TLS error, an unverified From address answers with a number, and
    "authentication failed" is usually the API key in the wrong box rather than a typo.
    """
    name, text = type(e).__name__, str(e)
    low = text.lower()
    raw = f"{name}: {text}"

    if isinstance(e, smtplib.SMTPAuthenticationError):
        return (f"{raw}\n\nThe server refused the login. For most services SMTP_PASSWORD is an "
                "API key rather than an account password, and SMTP_USER is a fixed word the "
                "service tells you -- 'resend', 'apikey', or the token itself -- not your "
                "e-mail address.")
    if isinstance(e, smtplib.SMTPSenderRefused):
        return (f"{raw}\n\nThe server would not send *from* this address. SMTP_FROM has to be "
                "one the service has verified, on a domain you have proved you own.")
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return (f"{raw}\n\nThe server would not send *to* that address. New accounts are often "
                "limited to addresses you have verified until the domain is approved.")
    if isinstance(e, smtplib.SMTPNotSupportedError) or "starttls" in low:
        return (f"{raw}\n\nPort {port} does not speak STARTTLS. Port 465 needs "
                "SMTP_SECURITY=ssl; port 587 needs starttls.")
    if isinstance(e, (smtplib.SMTPServerDisconnected, ssl.SSLError)) or "wrong version number" in low:
        return (f"{raw}\n\nThe connection broke before anything was sent, which is nearly always "
                f"the port and the security setting disagreeing: {port} with {security or 'starttls'}. "
                "Try 587 with starttls, or 465 with ssl.")
    if isinstance(e, socket.gaierror) or "name or service not known" in low or "nodename nor servname" in low:
        return f"{raw}\n\nThere is no such server as '{host}'. Check SMTP_HOST for a typo."
    if isinstance(e, (ConnectionRefusedError, TimeoutError)) or "timed out" in low or "refused" in low:
        # Render, Heroku and most of the cheap hosts block outgoing 25, 465 and 587 to keep
        # spammers off, and they do it by dropping the packets rather than refusing them -- so
        # it reads as a timeout, which reads as "wrong port".  It is not the port: 587 is right
        # and still times out.  2525 is the same service on a number nobody blocks, and every
        # mail provider worth using listens on it.  Saying "try 587" to somebody already on 587
        # is how an afternoon goes missing.
        also = " 2525 is the same service on a port that hosts do not block; try that next."
        if str(port) == "2525":
            also = (" You are already on 2525, so the host is blocking outgoing mail generally "
                    "and no port will help -- the provider's HTTP API is the way out.")
        return (f"{raw}\n\nNothing answered on {host}:{port}. Either the port is wrong, or the "
                f"host this site runs on blocks outgoing mail on it.{also}")
    return raw


def checks() -> list[tuple[str, str]]:
    """Setup problems worth saying before somebody presses Send test.

    Each is (level, sentence); "bad" stops mail going out at all, "warn" is a thing that will
    probably bite.
    """
    out: list[tuple[str, str]] = []
    host, sender = setting("SMTP_HOST"), setting("SMTP_FROM")
    if not host:
        out.append(("bad", "No SMTP_HOST, so nothing can be sent."))
    if not sender:
        out.append(("bad", "No SMTP_FROM, so there is no address to send from."))
    elif "@" not in sender:
        out.append(("bad", f"SMTP_FROM ('{sender}') is not an e-mail address."))
    port, security = setting("SMTP_PORT", "587"), setting("SMTP_SECURITY", "starttls").lower()
    if port == "465" and security != "ssl":
        out.append(("warn", "Port 465 wants SMTP_SECURITY=ssl; with starttls the connection "
                            "breaks before anything is sent."))
    if port in ("587", "2525") and security == "ssl":
        out.append(("warn", f"Port {port} wants SMTP_SECURITY=starttls, not ssl."))
    if setting("SMTP_USER") and not setting("SMTP_PASSWORD"):
        out.append(("warn", "SMTP_USER is set with no SMTP_PASSWORD."))
    if setting("SMTP_PASSWORD") and not setting("SMTP_USER"):
        out.append(("warn", "SMTP_PASSWORD is set with no SMTP_USER. Some services want the "
                            "token in both boxes."))
    if not base_url():
        out.append(("warn", "No PORTAL_BASE_URL, so password-reset e-mails will have no link "
                            "to click."))
    if not setting("REPORT_TO"):
        out.append(("warn", "No REPORT_TO, so the morning list has nowhere to go."))
    return out


def reset_email(username: str, link: str, minutes: int) -> tuple[str, str]:
    return ("Reset your Night Audit password",
            f"Hello {username},\n\n"
            f"Someone asked to reset the password for your Night Audit account.\n"
            f"Open this link within {minutes} minutes to choose a new one:\n\n    {link}\n\n"
            f"If that was not you, ignore this message. Your current password still works.\n")


def welcome_email(username: str, link: str, minutes: int, invited_by: str = "") -> tuple[str, str]:
    """What a newly imported account gets.  No password in it, only a link to choose one."""
    days = max(1, minutes // (24 * 60))
    return ("Your Night Audit login",
            f"Hello,\n\n"
            f"An account has been set up for you on the Night Audit portal"
            + (f" by {invited_by}" if invited_by else "") + ".\n\n"
            f"Your username is {username}. Choose a password here, within {days} "
            f"day{'s' if days != 1 else ''}:\n\n    {link}\n\n"
            f"The link works once. If it has expired by the time you get to it, use "
            f"'Forgotten your password?' on the sign-in page and a new one will be sent.\n")


def missing_uploads_email(day: str, missing: list[str], problems: list[str], link: Optional[str] = None) -> tuple[str, str]:
    lines = [f"Night audit for {day}", ""]
    lines += [f"Not uploaded ({len(missing)}): " + (", ".join(missing) or "none")]
    lines += [f"Uploaded but stuck ({len(problems)}): " + (", ".join(problems) or "none")]
    if link:
        lines += ["", link]
    return (f"Night audit {day}: {len(missing)} missing, {len(problems)} stuck", "\n".join(lines) + "\n")
