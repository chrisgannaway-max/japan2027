"""Users and sessions.

config/users.yaml::

    users:
      - username: admin
        password_hash: pbkdf2$...        # from `python -m portal hash <password>`
        role: admin
      - username: okcon
        password: changeme               # plain text is accepted for a first setup only
        role: manager
        properties: [OKCON]

Sessions are a signed cookie (HMAC with PORTAL_SECRET).  Managers only see and upload for
their own properties; admins see everything.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class User:
    username: str
    role: str                                  # "admin" | "manager"
    properties: list[str] = field(default_factory=list)
    password: Optional[str] = None
    password_hash: Optional[str] = None
    email: str = ""                            # needed for self-service password reset
    totp_secret: str = ""                      # set once the user enrols in MFA
    mfa_enabled: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def mfa_required(self) -> bool:
        """MFA is compulsory for the roles named in PORTAL_MFA_ROLES.

        Off by default: switching it on is a deliberate decision, and turning it on for a
        role before anyone has enrolled would otherwise lock that person out on first login.
        Set PORTAL_MFA_ROLES=admin in production.
        """
        roles = [r.strip() for r in os.environ.get("PORTAL_MFA_ROLES", "").split(",") if r.strip()]
        return self.role in roles

    def needs_mfa(self) -> bool:
        return self.mfa_enabled and bool(self.totp_secret)

    def check_password(self, candidate: str) -> bool:
        if self.password_hash:
            return verify_password(candidate, self.password_hash)
        return bool(self.password) and hmac.compare_digest(self.password, candidate)


def hash_password(password: str, salt: Optional[bytes] = None, rounds: int = 200_000) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(rounds))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:  # noqa: BLE001
        return False


class UserStore:
    def __init__(self, path: Optional[Path] = None, records: Optional[list[dict]] = None):
        self.path = Path(path) if path else None
        self.records = records
        self.users: dict[str, User] = {}
        self.reload()

    def reload(self, records: Optional[list[dict]] = None) -> None:
        if records is not None:
            self.records = records
        if self.records is not None:
            data = {"users": self.records}
        else:
            data = yaml.safe_load(self.path.read_text()) if self.path and self.path.exists() else {}
        self.users = {}
        for u in (data or {}).get("users", []):
            self.users[u["username"]] = User(
                username=u["username"], role=u.get("role", "manager"),
                properties=[str(p) for p in u.get("properties", [])],
                password=u.get("password"), password_hash=u.get("password_hash"),
                email=(u.get("email") or "").strip(), totp_secret=(u.get("totp_secret") or "").strip(),
                mfa_enabled=bool(u.get("mfa_enabled")))

    def authenticate(self, username: str, password: str) -> Optional[User]:
        u = self.users.get(username)
        return u if u and u.check_password(password) else None

    def get(self, username: str) -> Optional[User]:
        return self.users.get(username)

    def by_email(self, email: str) -> Optional[User]:
        email = (email or "").strip().lower()
        if not email:
            return None
        return next((u for u in self.users.values() if u.email.lower() == email), None)

    def find(self, who: str) -> Optional[User]:
        """By username or e-mail, for the 'forgot my password' form."""
        return self.get((who or "").strip()) or self.by_email(who)


class SessionSigner:
    def __init__(self, secret: Optional[str] = None, max_age: int = 12 * 3600):
        self.secret = (secret or os.environ.get("PORTAL_SECRET") or secrets.token_hex(32)).encode()
        self.max_age = max_age

    def sign(self, username: str) -> str:
        payload = f"{username}|{int(time.time()) + self.max_age}"
        sig = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()

    def verify(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        try:
            username, exp, sig = base64.urlsafe_b64decode(token.encode()).decode().split("|")
        except Exception:  # noqa: BLE001
            return None
        payload = f"{username}|{exp}"
        if not hmac.compare_digest(sig, hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()):
            return None
        return username if int(exp) > time.time() else None


# --------------------------------------------------------------------- MFA
def new_totp_secret() -> str:
    import pyotp
    return pyotp.random_base32()


def totp_uri(secret: str, username: str, issuer: str = "Night Audit to Odoo") -> str:
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def totp_qr_svg(uri: str) -> str:
    """An inline SVG QR code, so no image file has to be served or stored."""
    import io

    import qrcode
    import qrcode.image.svg
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def verify_totp(secret: str, code: str, drift: int = 1) -> bool:
    """`drift` windows either side, so a slightly wrong phone clock still works."""
    import pyotp
    code = re.sub(r"\s+", "", code or "")
    if not secret or not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=drift)


# --------------------------------------------------------------------- login throttling
class LoginThrottle:
    """Slows down password guessing.  In memory, so it resets when the process restarts and
    is per worker; that is enough to stop guessing without a shared store to maintain."""

    def __init__(self, max_failures: int = 5, per_ip: int = 20, window: int = 900, lockout: int = 900):
        self.max_failures, self.per_ip = max_failures, per_ip
        self.window, self.lockout = window, lockout
        self._fails: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        now = time.time()
        kept = [t for t in self._fails.get(key, []) if now - t < self.window]
        if kept:
            self._fails[key] = kept
        else:
            self._fails.pop(key, None)
        return kept

    def locked_for(self, ip: str, username: str) -> int:
        """Seconds still to wait, 0 if the attempt may proceed."""
        for key, limit in ((f"{ip}|{(username or '').lower()}", self.max_failures), (f"ip|{ip}", self.per_ip)):
            hits = self._recent(key)
            if len(hits) >= limit:
                return max(1, int(self.lockout - (time.time() - hits[-1])))
        return 0

    def record_failure(self, ip: str, username: str) -> None:
        now = time.time()
        for key in (f"{ip}|{(username or '').lower()}", f"ip|{ip}"):
            self._fails.setdefault(key, []).append(now)

    def record_success(self, ip: str, username: str) -> None:
        self._fails.pop(f"{ip}|{(username or '').lower()}", None)
