"""SQLite run history for the portal (stdlib only)."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path))
        c.row_factory = sqlite3.Row
        return c

    def add_run(self, *, uploaded_by: str, property_code: str, business_date: Optional[str], pms: str, ref: str,
                status: str, message: str, file_name: str, stored_path: str, result_json: str) -> int:
        with self._conn() as c:
            if ref:   # a new upload for the same property/day supersedes earlier, unposted runs
                c.execute("UPDATE runs SET superseded=1 WHERE ref=? AND posted_at IS NULL", (ref,))
            cur = c.execute(
                "INSERT INTO runs(created_at, uploaded_by, property_code, business_date, pms, ref, status, message, "
                "file_name, stored_path, result_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), uploaded_by, property_code, business_date, pms, ref,
                 status, message, file_name, stored_path, result_json))
            return int(cur.lastrowid)

    def get_run(self, run_id: int) -> Optional[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

    def runs_for_date(self, business_date: str) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM runs WHERE business_date=? AND superseded=0 ORDER BY property_code, id DESC",
                             (business_date,)).fetchall()

    def recent_runs(self, limit: int = 50, property_codes: Optional[list[str]] = None) -> list[sqlite3.Row]:
        with self._conn() as c:
            if property_codes is not None:
                marks = ",".join("?" * len(property_codes)) or "''"
                return c.execute(f"SELECT * FROM runs WHERE property_code IN ({marks}) ORDER BY id DESC LIMIT ?",
                                 (*property_codes, limit)).fetchall()
            return c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def mark(self, run_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))

    def dates_with_runs(self, limit: int = 30) -> list[str]:
        with self._conn() as c:
            return [r[0] for r in c.execute(
                "SELECT DISTINCT business_date FROM runs WHERE business_date IS NOT NULL ORDER BY business_date DESC LIMIT ?",
                (limit,))]

    # ---------------------------------------------------------------- invoices
    INVOICE_FIELDS = ("property_code", "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date",
                      "subtotal", "tax_amount", "total", "account_code", "description", "notes")

    def add_invoice(self, *, uploaded_by: str, file_name: str, stored_path: str, reader: str, confidence: str,
                    **fields) -> int:
        cols = ["created_at", "uploaded_by", "file_name", "stored_path", "reader", "confidence"] + list(fields)
        vals = [datetime.now().isoformat(timespec="seconds"), uploaded_by, file_name, stored_path, reader, confidence] + list(fields.values())
        with self._conn() as c:
            cur = c.execute(f"INSERT INTO invoices({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", vals)
            return int(cur.lastrowid)

    def get_invoice(self, inv_id: int) -> Optional[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()

    def update_invoice(self, inv_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE invoices SET {cols} WHERE id=?", (*fields.values(), inv_id))

    def list_invoices(self, status: Optional[str] = None, property_codes: Optional[list[str]] = None,
                      limit: int = 200) -> list[sqlite3.Row]:
        where, params = [], []
        if status:
            where.append("status=?"); params.append(status)
        if property_codes is not None:
            marks = ",".join("?" * len(property_codes)) or "''"
            where.append(f"property_code IN ({marks})"); params.extend(property_codes)
        sql = "SELECT * FROM invoices" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        with self._conn() as c:
            return c.execute(sql, (*params, limit)).fetchall()

    def find_duplicate_invoice(self, vendor_name: str, invoice_number: str, exclude_id: int = 0) -> Optional[sqlite3.Row]:
        if not vendor_name or not invoice_number:
            return None
        with self._conn() as c:
            return c.execute("SELECT * FROM invoices WHERE lower(vendor_name)=lower(?) AND invoice_number=? AND id<>? "
                             "AND status<>'rejected' ORDER BY id LIMIT 1", (vendor_name, invoice_number, exclude_id)).fetchone()
