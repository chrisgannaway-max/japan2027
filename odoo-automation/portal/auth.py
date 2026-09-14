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

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

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
    def __init__(self, path: Path):
        self.path = Path(path)
        self.users: dict[str, User] = {}
        self.reload()

    def reload(self) -> None:
        data = yaml.safe_load(self.path.read_text()) if self.path.exists() else {}
        self.users = {}
        for u in (data or {}).get("users", []):
            self.users[u["username"]] = User(username=u["username"], role=u.get("role", "manager"),
                                             properties=[str(p) for p in u.get("properties", [])],
                                             password=u.get("password"), password_hash=u.get("password_hash"))

    def authenticate(self, username: str, password: str) -> Optional[User]:
        u = self.users.get(username)
        return u if u and u.check_password(password) else None

    def get(self, username: str) -> Optional[User]:
        return self.users.get(username)


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
