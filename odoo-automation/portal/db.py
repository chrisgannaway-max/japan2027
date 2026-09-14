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
