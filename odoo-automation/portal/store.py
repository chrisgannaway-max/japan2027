"""Where configuration lives: YAML files (default) or the portal database.

    PORTAL_STORE=yaml   properties.yaml, users.yaml, gl_mapping/*.yaml  (edit files, restart)
    PORTAL_STORE=db     tables `properties`, `users` (+ mapping YAML per property) edited on
                        the admin screens; seeded once from the files with `import_from_yaml`.

Both stores hand the rest of the app the same shapes: a properties dict like
pipeline.load_properties() returns, and a list of user dicts for auth.UserStore.  In db
mode each property's mapping YAML is materialised to data/mappings/<code>.yaml so the
pipeline keeps reading mappings from a path.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from pms_to_odoo.mapping import GLMapping, MappingError
from pms_to_odoo.pipeline import load_properties as load_properties_yaml, resolve

from . import clock
from .sql import Pool, database_url

from .auth import hash_password

PROPERTY_COLUMNS = ("code", "name", "brand", "pms", "pms_property_id", "pms_property_name", "company", "analytic",
                    "journal", "mapping_yaml", "enabled")
SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
    code TEXT PRIMARY KEY, name TEXT, brand TEXT, pms TEXT NOT NULL, pms_property_id TEXT, pms_property_name TEXT,
    company TEXT, analytic TEXT, journal TEXT, mapping_yaml TEXT, enabled INTEGER DEFAULT 1, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL, properties TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1, updated_at TEXT, email TEXT DEFAULT '', totp_secret TEXT DEFAULT '',
    mfa_enabled INTEGER DEFAULT 0
);
"""


def store_mode() -> str:
    return "db" if os.environ.get("PORTAL_STORE", "yaml").lower() == "db" else "yaml"


class ConfigStore:
    """Database-backed properties and users (used when PORTAL_STORE=db)."""

    def __init__(self, db_path: Path, data_dir: Path):
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        url = str(db_path)
        if not url.startswith(("sqlite:///", "postgres://", "postgresql://")):
            url = database_url(Path(db_path).parent)
        self.pool = Pool(url)
        with self._conn() as c:
            c.executescript(SCHEMA)
            for col, decl in (("email", "TEXT DEFAULT ''"), ("totp_secret", "TEXT DEFAULT ''"),
                              ("mfa_enabled", "INTEGER DEFAULT 0")):
                c.add_column_if_missing("users", col, decl)   # databases created before MFA existed

    def _conn(self):
        return self.pool.connect()

    # ------------------------------------------------------------- properties
    def properties(self, include_disabled: bool = False) -> dict[str, dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM properties" + ("" if include_disabled else " WHERE enabled=1") + " ORDER BY code").fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            p = {k: r[k] for k in PROPERTY_COLUMNS}
            p["_config_dir"] = self.data_dir
            p["gl_mapping"] = str(self._mapping_path(r["code"]))
            if r["mapping_yaml"] and not Path(p["gl_mapping"]).exists():
                self._materialise(r["code"], r["mapping_yaml"])
            out[r["code"]] = p
        return out

    def get_property(self, code: str) -> Optional[dict]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM properties WHERE code=?", (code,)).fetchone()
        return {k: r[k] for k in PROPERTY_COLUMNS} if r else None

    def save_property(self, **fields) -> None:
        code = fields["code"].strip()
        if not code or not fields.get("pms"):
            raise ValueError("code and pms are required")
        mapping_yaml = fields.get("mapping_yaml") or ""
        if mapping_yaml.strip():
            self._validate_mapping(mapping_yaml)
        vals = {k: (fields.get(k) or "") for k in PROPERTY_COLUMNS if k not in ("enabled",)}
        vals["code"] = code
        vals["pms"] = str(vals["pms"]).upper()
        vals["enabled"] = 1 if str(fields.get("enabled", "1")) in ("1", "on", "true", "True") else 0
        vals["updated_at"] = clock.stamp()
        cols = ", ".join(vals)
        with self._conn() as c:
            c.execute(f"INSERT INTO properties({cols}) VALUES({', '.join('?' * len(vals))}) ON CONFLICT(code) DO UPDATE SET "
                      + ", ".join(f"{k}=excluded.{k}" for k in vals if k != "code"), tuple(vals.values()))
        self._materialise(code, mapping_yaml)

    def delete_property(self, code: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM properties WHERE code=?", (code,))
        p = self._mapping_path(code)
        if p.exists():
            p.unlink()

    def _mapping_path(self, code: str) -> Path:
        return self.data_dir / "mappings" / f"{code}.yaml"

    def _materialise(self, code: str, mapping_yaml: str) -> None:
        p = self._mapping_path(code)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(mapping_yaml or "rules: []\n")

    @staticmethod
    def _validate_mapping(text: str) -> None:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write(text)
            tmp = fh.name
        try:
            GLMapping.load(tmp)
        except (MappingError, yaml.YAMLError) as e:
            raise ValueError(f"Mapping YAML is invalid: {e}") from e
        finally:
            Path(tmp).unlink(missing_ok=True)

    # ------------------------------------------------------------- users
    def users(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM users WHERE enabled=1 ORDER BY username").fetchall()
        return [{"username": r["username"], "password_hash": r["password_hash"], "role": r["role"],
                 "properties": [p for p in (r["properties"] or "").split(",") if p],
                 "email": r["email"] or "", "totp_secret": r["totp_secret"] or "",
                 "mfa_enabled": bool(r["mfa_enabled"])} for r in rows]

    def all_users(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [dict(r) for r in rows]

    def save_user(self, username: str, role: str, properties: list[str], password: Optional[str] = None,
                  password_hash: Optional[str] = None, enabled: bool = True, email: Optional[str] = None) -> None:
        username = username.strip()
        if not username or role not in ("admin", "manager"):
            raise ValueError("username and a role of admin or manager are required")
        with self._conn() as c:
            existing = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            ph = password_hash or (hash_password(password) if password else (existing["password_hash"] if existing else None))
            if not ph:
                raise ValueError("a password is required for a new user")
            mail = (email if email is not None else (existing["email"] if existing else "")) or ""
            c.execute("INSERT INTO users(username, password_hash, role, properties, enabled, updated_at, email) "
                      "VALUES(?,?,?,?,?,?,?) "
                      "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role=excluded.role, "
                      "properties=excluded.properties, enabled=excluded.enabled, updated_at=excluded.updated_at, "
                      "email=excluded.email",
                      (username, ph, role, ",".join(p.strip() for p in properties if p.strip()), 1 if enabled else 0,
                       clock.stamp(), mail.strip()))

    def set_password(self, username: str, password: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE users SET password_hash=?, updated_at=? WHERE username=?",
                      (hash_password(password), clock.stamp(), username))

    def set_mfa(self, username: str, secret: str, enabled: bool) -> None:
        with self._conn() as c:
            c.execute("UPDATE users SET totp_secret=?, mfa_enabled=?, updated_at=? WHERE username=?",
                      (secret, 1 if enabled else 0, clock.stamp(), username))

    def delete_user(self, username: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM users WHERE username=?", (username,))

    # ------------------------------------------------------------- seeding
    def import_from_yaml(self, properties_path: Path, users_path: Path, overwrite: bool = False) -> dict[str, int]:
        """Seed the tables from the config files.  Existing rows are kept unless overwrite."""
        counts = {"properties": 0, "users": 0}
        existing_props = set(self.properties(include_disabled=True))
        for code, p in load_properties_yaml(properties_path).items():
            if code in existing_props and not overwrite:
                continue
            mp = resolve(p, "gl_mapping")
            self.save_property(code=code, name=p.get("name", ""), brand=p.get("brand", ""), pms=p.get("pms", ""),
                               pms_property_id=str(p.get("pms_property_id", "") or ""), pms_property_name=p.get("pms_property_name", ""),
                               company=p.get("company", ""), analytic=p.get("analytic", ""), journal=p.get("journal", ""),
                               mapping_yaml=(mp.read_text() if mp and mp.exists() else ""), enabled="1")
            counts["properties"] += 1
        existing_users = {u["username"] for u in self.all_users()}
        users_file = users_path if users_path.exists() else users_path.with_name("users.example.yaml")
        data = yaml.safe_load(users_file.read_text()) if users_file.exists() else {}
        for u in (data or {}).get("users", []):
            if u["username"] in existing_users and not overwrite:
                continue
            self.save_user(u["username"], u.get("role", "manager"), [str(x) for x in u.get("properties", [])],
                           password=u.get("password"), password_hash=u.get("password_hash"),
                           email=(u.get("email") or ""))
            counts["users"] += 1
        return counts


# --------------------------------------------------------------------- mapping text
def read_mapping_text(prop: dict, store: Optional[ConfigStore]) -> str:
    """A property's mapping YAML, from the database in db mode or from its file in yaml mode."""
    if store is not None:
        row = store.get_property(prop["code"])
        if row is not None:
            return row.get("mapping_yaml") or ""
    path = resolve(prop, "gl_mapping")
    return path.read_text() if path and path.exists() else ""


def write_mapping_text(prop: dict, store: Optional[ConfigStore], text: str) -> str:
    """Save it back, validating first.  Returns where it was written, for the audit trail."""
    ConfigStore._validate_mapping(text)
    if store is not None:
        row = store.get_property(prop["code"]) or {}
        store.save_property(**{**{k: row.get(k) or "" for k in PROPERTY_COLUMNS},
                               "code": prop["code"], "pms": row.get("pms") or prop.get("pms", ""),
                               "mapping_yaml": text, "enabled": "1" if row.get("enabled", 1) else "0"})
        return f"database ({prop['code']})"
    path = resolve(prop, "gl_mapping")
    if not path:
        raise ValueError(f"{prop['code']}: no gl_mapping file configured")
    path.write_text(text)
    return str(path)
