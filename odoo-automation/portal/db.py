"""Run history, invoices, accounts and settings.

Works on SQLite (the default, a file) or PostgreSQL (set DATABASE_URL); see portal/sql.py."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import clock
from .sql import Pool, database_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id {ID},
    created_at TEXT NOT NULL,
    uploaded_by TEXT,
    property_code TEXT,
    business_date TEXT,
    pms TEXT,
    ref TEXT,
    status TEXT NOT NULL,
    message TEXT,
    file_name TEXT,
    stored_path TEXT,
    result_json TEXT NOT NULL,
    approved_at TEXT,
    approved_by TEXT,
    exported_at TEXT,
    posted_at TEXT,
    odoo_move_id INTEGER,
    superseded INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_prop_date ON runs(property_code, business_date);
CREATE TABLE IF NOT EXISTS invoices (
    id {ID},
    created_at TEXT NOT NULL,
    uploaded_by TEXT,
    property_code TEXT,
    vendor_name TEXT,
    vendor_tax_id TEXT,
    invoice_number TEXT,
    invoice_date TEXT,
    due_date TEXT,
    subtotal TEXT,
    tax_amount TEXT,
    total TEXT,
    account_code TEXT,
    description TEXT,
    notes TEXT,
    file_name TEXT,
    stored_path TEXT,
    reader TEXT,
    confidence TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    approved_at TEXT,
    approved_by TEXT,
    exported_at TEXT,
    posted_at TEXT,
    odoo_move_id INTEGER
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
    code TEXT PRIMARY KEY,
    name TEXT,
    account_type TEXT,
    source TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS intake (
    sha256 TEXT PRIMARY KEY,
    message_id TEXT,
    sender TEXT,
    recipient TEXT,
    subject TEXT,
    received_at TEXT,
    file_name TEXT,
    stored_path TEXT,
    run_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intake_message ON intake(message_id);
CREATE TABLE IF NOT EXISTS vendor_accounts (
    vendor_key TEXT PRIMARY KEY,
    vendor_name TEXT,
    account_code TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path_or_url: Any):
        url = str(path_or_url)
        if not url.startswith(("sqlite:///", "postgres://", "postgresql://")):
            url = database_url(Path(path_or_url).parent) if Path(path_or_url).name == "portal.db" \
                else f"sqlite:///{path_or_url}"
        self.pool = Pool(url)
        self.path = getattr(self.pool, "path", None)
        with self._conn() as c:
            c.executescript(SCHEMA)
            for col, decl in (("post_attempts", "INTEGER DEFAULT 0"), ("post_error", "TEXT"),
                              ("notified_at", "TEXT")):
                c.add_column_if_missing("runs", col, decl)   # databases made before the queue

    def _conn(self):
        return self.pool.connect()

    def describe(self) -> str:
        return self.pool.describe()

    # ------------------------------------------------------------- arriving e-mail
    def intake_seen(self, sha256: str) -> Optional[dict]:
        """The row for an attachment we have already taken in, or None.  Keyed on the bytes, so
        the same report forwarded three times by three people is still seen once."""
        with self._conn() as c:
            r = c.execute("SELECT * FROM intake WHERE sha256=?", (sha256,)).fetchone()
        return dict(r) if r else None

    def record_intake(self, *, sha256: str, message_id: str, sender: str, recipient: str,
                      subject: str, received_at: Optional[str], file_name: str,
                      stored_path: str, run_id: Optional[int]) -> None:
        with self._conn() as c:
            c.upsert("intake",
                     ("sha256", "message_id", "sender", "recipient", "subject", "received_at",
                      "file_name", "stored_path", "run_id", "created_at"),
                     (sha256, message_id, sender, recipient, subject, received_at, file_name,
                      stored_path, run_id, clock.stamp()),
                     conflict="sha256")

    def intake_for_run(self, run_id: int) -> Optional[dict]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM intake WHERE run_id=?", (run_id,)).fetchone()
        return dict(r) if r else None

    def add_run(self, *, uploaded_by: str, property_code: str, business_date: Optional[str], pms: str, ref: str,
                status: str, message: str, file_name: str, stored_path: str, result_json: str) -> int:
        with self._conn() as c:
            if ref:   # a new upload for the same property/day supersedes earlier, unposted runs
                c.execute("UPDATE runs SET superseded=1 WHERE ref=? AND posted_at IS NULL", (ref,))
            return c.insert(
                "INSERT INTO runs(created_at, uploaded_by, property_code, business_date, pms, ref, status, message, "
                "file_name, stored_path, result_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (clock.stamp(), uploaded_by, property_code, business_date, pms, ref,
                 status, message, file_name, stored_path, result_json))

    def runs_not_yet_notified(self, statuses: tuple = ("unmapped",)) -> list[dict]:
        """Live runs in one of these states that nobody has been told about yet."""
        marks = ",".join("?" * len(statuses))
        with self._conn() as c:
            return c.execute(f"SELECT * FROM runs WHERE status IN ({marks}) AND superseded=0 "
                             "AND notified_at IS NULL ORDER BY id", statuses).fetchall()

    def mark_notified(self, run_ids: list[int]) -> None:
        if not run_ids:
            return
        now = clock.stamp()
        with self._conn() as c:
            for rid in run_ids:
                c.execute("UPDATE runs SET notified_at=? WHERE id=?", (now, rid))

    def awaiting_companion(self, property_code: str, business_date: Optional[str]) -> Optional[dict]:
        """The live run for this night that is still waiting for the other half of its report."""
        if not (property_code and business_date):
            return None
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE property_code=? AND business_date=? "
                          "AND status='awaiting_companion' AND superseded=0 ORDER BY id DESC LIMIT 1",
                          (property_code, business_date)).fetchone()
        return dict(r) if r else None

    def get_run(self, run_id: int) -> Optional[dict]:
        with self._conn() as c:
            return c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

    def runs_for_date(self, business_date: str) -> list[dict]:
        with self._conn() as c:
            return c.execute("SELECT * FROM runs WHERE business_date=? AND superseded=0 ORDER BY property_code, id DESC",
                             (business_date,)).fetchall()

    def runs_between(self, start: str, end: str) -> list[dict]:
        """Live (non-superseded) runs with a business date in [start, end]."""
        with self._conn() as c:
            return c.execute("SELECT * FROM runs WHERE superseded=0 AND business_date BETWEEN ? AND ? "
                             "ORDER BY business_date, property_code, id", (start, end)).fetchall()

    def first_business_date(self) -> dict[str, str]:
        """property_code -> its earliest business date, so a hotel is not marked missing
        for the days before it was onboarded."""
        with self._conn() as c:
            rows = c.execute("SELECT property_code, MIN(business_date) first FROM runs "
                             "WHERE business_date IS NOT NULL GROUP BY property_code").fetchall()
        return {r["property_code"]: r["first"] for r in rows if r["property_code"]}

    def recent_runs(self, limit: int = 50, property_codes: Optional[list[str]] = None) -> list[dict]:
        with self._conn() as c:
            if property_codes is not None:
                marks = ",".join("?" * len(property_codes)) or "''"
                return c.execute(f"SELECT * FROM runs WHERE property_code IN ({marks}) ORDER BY id DESC LIMIT ?",
                                 (*property_codes, limit)).fetchall()
            return c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # ------------------------------------------------------------- the posting queue
    def runs_awaiting_post(self, limit: int = 50, max_attempts: int = 5) -> list[dict]:
        """Balanced nights that have not reached Odoo yet, oldest first.

        Deliberately derived from what is already true -- not yet posted, still the live run for
        its night, balanced -- rather than from a separate status column that could disagree with
        them.  A run that has failed too often drops out and waits for a person.
        """
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM runs WHERE status='ok' AND superseded=0 AND posted_at IS NULL "
                "AND COALESCE(post_attempts, 0) < ? ORDER BY id LIMIT ?",
                (max_attempts, limit)).fetchall()

    def runs_stuck(self, max_attempts: int = 5) -> list[dict]:
        """Balanced nights that gave up trying.  These need a person, so they belong on the
        daily report next to the ones that never arrived."""
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM runs WHERE status='ok' AND superseded=0 AND posted_at IS NULL "
                "AND COALESCE(post_attempts, 0) >= ? ORDER BY business_date, property_code",
                (max_attempts,)).fetchall()

    def record_post_failure(self, run_id: int, error: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE runs SET post_attempts = COALESCE(post_attempts, 0) + 1, post_error=? "
                      "WHERE id=?", (error[:500], run_id))

    def mark(self, run_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))

    def dates_with_runs(self, limit: int = 30) -> list[str]:
        with self._conn() as c:
            return [r["business_date"] for r in c.execute(
                "SELECT DISTINCT business_date FROM runs WHERE business_date IS NOT NULL "
                "ORDER BY business_date DESC LIMIT ?", (limit,)).fetchall()]

    # ---------------------------------------------------------------- invoices
    INVOICE_FIELDS = ("property_code", "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date",
                      "subtotal", "tax_amount", "total", "account_code", "description", "notes")

    def add_invoice(self, *, uploaded_by: str, file_name: str, stored_path: str, reader: str, confidence: str,
                    **fields) -> int:
        cols = ["created_at", "uploaded_by", "file_name", "stored_path", "reader", "confidence"] + list(fields)
        vals = [clock.stamp(), uploaded_by, file_name, stored_path, reader, confidence] + list(fields.values())
        with self._conn() as c:
            return c.insert(f"INSERT INTO invoices({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", vals)

    def get_invoice(self, inv_id: int) -> Optional[dict]:
        with self._conn() as c:
            return c.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()

    def update_invoice(self, inv_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE invoices SET {cols} WHERE id=?", (*fields.values(), inv_id))

    def list_invoices(self, status: Optional[str] = None, property_codes: Optional[list[str]] = None,
                      limit: int = 200) -> list[dict]:
        where, params = [], []
        if status:
            where.append("status=?"); params.append(status)
        if property_codes is not None:
            marks = ",".join("?" * len(property_codes)) or "''"
            where.append(f"property_code IN ({marks})"); params.extend(property_codes)
        sql = "SELECT * FROM invoices" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        with self._conn() as c:
            return c.execute(sql, (*params, limit)).fetchall()

    def find_duplicate_invoice(self, vendor_name: str, invoice_number: str, exclude_id: int = 0) -> Optional[dict]:
        if not vendor_name or not invoice_number:
            return None
        with self._conn() as c:
            return c.execute("SELECT * FROM invoices WHERE lower(vendor_name)=lower(?) AND invoice_number=? AND id<>? "
                             "AND status<>'rejected' ORDER BY id LIMIT 1", (vendor_name, invoice_number, exclude_id)).fetchone()

    # ---------------------------------------------------------------- vendor memory
    @staticmethod
    def vendor_key(name: str) -> str:
        import re as _re
        return _re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()

    def remembered_account(self, vendor_name: str) -> Optional[str]:
        key = self.vendor_key(vendor_name)
        if not key:
            return None
        with self._conn() as c:
            row = c.execute("SELECT account_code FROM vendor_accounts WHERE vendor_key=?", (key,)).fetchone()
        return row["account_code"] if row else None

    def remember_account(self, vendor_name: str, account_code: str) -> None:
        key = self.vendor_key(vendor_name)
        if not key or not account_code:
            return
        with self._conn() as c:
            c.execute("INSERT INTO vendor_accounts(vendor_key, vendor_name, account_code, updated_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(vendor_key) DO UPDATE SET account_code=excluded.account_code, vendor_name=excluded.vendor_name, "
                      "updated_at=excluded.updated_at", (key, vendor_name, account_code, clock.stamp()))

    # ---------------------------------------------------------------- chart of accounts
    def replace_accounts(self, rows: list[dict], source: str) -> int:
        """Replace the stored chart of accounts. rows: {code, name, account_type}."""
        now = clock.stamp()
        clean = [(str(r["code"]).strip(), (r.get("name") or "").strip(),
                  (r.get("account_type") or "").strip(), source, now)
                 for r in rows if str(r.get("code") or "").strip()]
        with self._conn() as c:
            c.execute("DELETE FROM accounts")
            for row in clean:
                c.upsert("accounts", ("code", "name", "account_type", "source", "updated_at"), row, "code")
        return len(clean)

    def list_accounts(self, q: str = "", limit: int = 2000) -> list[dict]:
        with self._conn() as c:
            if q:
                like = f"%{q}%"
                return c.execute("SELECT * FROM accounts WHERE code LIKE ? OR name LIKE ? ORDER BY code LIMIT ?",
                                 (like, like, limit)).fetchall()
            return c.execute("SELECT * FROM accounts ORDER BY code LIMIT ?", (limit,)).fetchall()

    def accounts_info(self) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) n, MAX(updated_at) at, MAX(source) src FROM accounts").fetchone()
        return {"count": row["n"], "updated_at": row["at"], "source": row["src"]}

    # ---------------------------------------------------------------- password resets
    @staticmethod
    def _token_hash(token: str) -> str:
        import hashlib
        return hashlib.sha256(token.encode()).hexdigest()

    def create_reset(self, username: str, token: str, minutes: int = 60) -> None:
        """Only the hash is stored, so the database never holds a usable link."""
        from datetime import timedelta as _td
        # Both ends in UTC, because the expiry is checked by comparing the two strings -- and
        # an hour that local time runs twice would hand out a two-hour link.
        now = clock.utc_now()
        with self._conn() as c:
            c.execute("DELETE FROM password_resets WHERE username=? AND used_at IS NULL", (username,))
            c.execute("INSERT INTO password_resets(token_hash, username, created_at, expires_at) VALUES(?,?,?,?)",
                      (self._token_hash(token), username, now.strftime("%Y-%m-%dT%H:%M:%S"),
                       (now + _td(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")))

    def use_reset(self, token: str) -> Optional[str]:
        """The username if the token is valid and unused, else None.  Single use."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM password_resets WHERE token_hash=?", (self._token_hash(token),)).fetchone()
            if row is None or row["used_at"] or row["expires_at"] < clock.stamp():
                return None
            c.execute("UPDATE password_resets SET used_at=? WHERE token_hash=?",
                      (clock.stamp(), row["token_hash"]))
            return row["username"]

    def peek_reset(self, token: str) -> Optional[str]:
        """Like use_reset but without spending it, so the form can be shown first."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM password_resets WHERE token_hash=?", (self._token_hash(token),)).fetchone()
        if row is None or row["used_at"] or row["expires_at"] < clock.stamp():
            return None
        return row["username"]

    # ---------------------------------------------------------------- settings
    def settings(self) -> dict[str, str]:
        with self._conn() as c:
            return {r["key"]: (r["value"] or "") for r in c.execute("SELECT key, value FROM settings")}

    def save_settings(self, values: dict[str, str], username: str = "") -> None:
        """Blank values are removed rather than stored, so falling back to the environment
        is always possible."""
        now = clock.stamp()
        with self._conn() as c:
            for k, v in values.items():
                if v is None or v == "":
                    c.execute("DELETE FROM settings WHERE key=?", (k,))
                else:
                    c.execute("INSERT INTO settings(key, value, updated_at, updated_by) VALUES(?,?,?,?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, "
                              "updated_by=excluded.updated_by", (k, v, now, username))
