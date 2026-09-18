"""One small database layer over SQLite and PostgreSQL.

SQLite is the default and needs nothing configured: the portal keeps a file under its data
directory. Set `DATABASE_URL` to a `postgres://` or `postgresql://` address (Supabase, RDS,
anything) and the same code runs there instead.

Rather than pull in an ORM, this translates the handful of things that genuinely differ:

* placeholders, written as `?` throughout and rewritten to `%s` for PostgreSQL
* the auto-incrementing primary key, written as `{ID}` in the schema
* fetching the id of a row just inserted, `lastrowid` against `RETURNING id`
* upserts that SQLite spells `INSERT OR REPLACE`
* asking which columns a table has, for the small migrations

Rows come back addressable by name in both, so callers just use `row["column"]`.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

SQLITE, POSTGRES = "sqlite", "postgres"


def database_url(data_dir: Optional[Path] = None) -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return url
    return f"sqlite:///{Path(data_dir or '.') / 'portal.db'}"


def dialect_of(url: str) -> str:
    return POSTGRES if url.startswith(("postgres://", "postgresql://")) else SQLITE


def _to_pg_placeholders(sql: str) -> str:
    """`?` -> `%s`, leaving anything inside quotes alone, and escaping literal percents."""
    out, in_single, in_double = [], False, False
    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        if not in_single and not in_double:
            if ch == "?":
                out.append("%s")
                continue
            if ch == "%":
                out.append("%%")
                continue
        out.append(ch)
    return "".join(out)


class Cursor:
    """Just enough of a cursor that callers can iterate it or call fetchone/fetchall."""

    def __init__(self, raw):
        self._raw = raw

    def fetchone(self):
        return self._raw.fetchone()

    def fetchall(self):
        return self._raw.fetchall()

    def __iter__(self):
        return iter(self._raw.fetchall())

    @property
    def lastrowid(self):
        return self._raw.lastrowid


class Conn:
    """A connection that speaks the same dialect-free SQL either way.

    Used as a context manager; the transaction commits on a clean exit and rolls back if
    the block raises.
    """

    def __init__(self, raw, dialect: str):
        self._raw, self.dialect = raw, dialect

    # -- context manager ------------------------------------------------------
    def __enter__(self) -> "Conn":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self._raw.close()
        return False

    # -- statements -----------------------------------------------------------
    def _sql(self, sql: str) -> str:
        return _to_pg_placeholders(sql) if self.dialect == POSTGRES else sql

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        cur = self._raw.cursor() if self.dialect == POSTGRES else self._raw
        return Cursor(cur.execute(self._sql(sql), tuple(params)))

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        rows = [tuple(r) for r in rows]
        if not rows:
            return
        if self.dialect == POSTGRES:
            with self._raw.cursor() as cur:
                cur.executemany(self._sql(sql), rows)
        else:
            self._raw.executemany(sql, rows)

    def executescript(self, script: str) -> None:
        """Run a schema. `{ID}` becomes the dialect's auto-incrementing primary key."""
        script = script.replace("{ID}", "SERIAL PRIMARY KEY" if self.dialect == POSTGRES
                                else "INTEGER PRIMARY KEY AUTOINCREMENT")
        if self.dialect == SQLITE:
            self._raw.executescript(script)
            return
        with self._raw.cursor() as cur:
            for statement in [s.strip() for s in script.split(";") if s.strip()]:
                cur.execute(statement)

    def insert(self, sql: str, params: Sequence[Any] = (), pk: str = "id") -> int:
        """Insert one row and return its new primary key."""
        if self.dialect == POSTGRES:
            with self._raw.cursor() as cur:
                cur.execute(self._sql(f"{sql.rstrip().rstrip(';')} RETURNING {pk}"), tuple(params))
                return int(cur.fetchone()[pk])
        return int(self._raw.execute(sql, tuple(params)).lastrowid)

    def upsert(self, table: str, columns: Sequence[str], values: Sequence[Any], conflict: str) -> None:
        """`INSERT OR REPLACE` in SQLite terms, written once for both."""
        cols = ", ".join(columns)
        marks = ", ".join("?" * len(columns))
        sets = ", ".join(f"{c}=excluded.{c}" for c in columns if c != conflict)
        self.execute(f"INSERT INTO {table}({cols}) VALUES({marks}) "
                     f"ON CONFLICT({conflict}) DO UPDATE SET {sets}", values)

    def columns(self, table: str) -> set[str]:
        if self.dialect == POSTGRES:
            # Only our own schema.  A bare table_name matches every schema in the database, and
            # a Supabase project ships with auth.users, storage.objects and more.  Asking whether
            # "users" has an "email" column would find auth.users.email and answer yes about a
            # table we never touch -- so the column would not be added and every login would then
            # fail looking for it.
            rows = self.execute("SELECT column_name FROM information_schema.columns "
                                "WHERE table_name=? AND table_schema=current_schema()",
                                (table,)).fetchall()
            return {r["column_name"] for r in rows}
        return {r["name"] for r in self.execute(f"PRAGMA table_info({table})").fetchall()}

    def add_column_if_missing(self, table: str, column: str, decl: str) -> bool:
        if column in self.columns(table):
            return False
        self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True


class Pool:
    """Hands out connections.  SQLite opens the file per call, which suits a low-traffic
    site; PostgreSQL opens a connection per call too, which a hosted database handles well
    at this volume and keeps the code free of pool lifecycle."""

    def __init__(self, url: str):
        self.url = url
        self.dialect = dialect_of(url)
        if self.dialect == SQLITE:
            self.path = Path(url.replace("sqlite:///", "", 1))
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> Conn:
        if self.dialect == POSTGRES:
            import psycopg
            from psycopg.rows import dict_row
            try:
                raw = psycopg.connect(self.url, row_factory=dict_row, autocommit=False)
            except psycopg.OperationalError as e:
                raise psycopg.OperationalError(f"{e}{self._hint(str(e))}") from None
            # Never prepare statements.  psycopg starts preparing a query after it has been seen
            # a few times, which breaks behind a transaction-mode pooler (PgBouncer, Supabase's
            # 6543 port): the prepared statement belongs to a server connection the next query
            # may not get, and it fails as "prepared statement already exists" under load rather
            # than at once.  A night audit is a few dozen queries a day; preparing them buys
            # nothing, and not preparing them means any connection string works.
            raw.prepare_threshold = None
            return Conn(raw, POSTGRES)
        raw = sqlite3.connect(str(self.path))
        raw.row_factory = sqlite3.Row
        return Conn(raw, SQLITE)

    def _hint(self, message: str) -> str:
        """Turn the two connection failures that actually happen into instructions.

        Both look like something they are not.  A Supabase direct connection resolves to IPv6
        only unless the project pays for the IPv4 add-on, so a host that speaks IPv4 -- Render,
        most of them -- gets "Network is unreachable" against a raw address, which reads like the
        database is down.  And a password with a symbol in it silently truncates the URL, which
        reads like the wrong password.
        """
        low = message.lower()
        # `@db.<ref>.supabase.co` is the direct connection.  Matching on ".supabase.co" alone
        # would also match the pooler, whose host ends .supabase.com -- and telling somebody to
        # switch to the thing they are already using is worse than saying nothing.
        if "network is unreachable" in low and re.search(r"@db\.[a-z0-9]+\.supabase\.co\b", self.url):
            return ("\n\nHINT: that is Supabase's direct connection, which is reachable over IPv6 "
                    "only unless the project has the IPv4 add-on. Most hosts are IPv4. Use the "
                    "session pooler string instead: press Connect in the Supabase dashboard and "
                    "take the Session pooler URI -- the user gains the project ref "
                    "(postgres.<ref>) and the host becomes <region>.pooler.supabase.com.")
        # More than one "@" between the scheme and the path means the password contains one
        # unencoded, which ends the userinfo early and sends a truncated password.
        authority = self.url.split("://", 1)[-1].split("/", 1)[0]
        if "password authentication failed" in low and authority.count("@") > 1:
            return ("\n\nHINT: the password appears to contain a character that has a meaning "
                    "inside a URL. Percent-encode it -- @ becomes %40, # becomes %23, / becomes "
                    "%2F, : becomes %3A -- or set a password of letters and digits only.")
        return ""

    def describe(self) -> str:
        if self.dialect == SQLITE:
            return f"SQLite at {self.path}"
        return "PostgreSQL " + re.sub(r"://[^@]*@", "://***@", self.url)
