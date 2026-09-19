"""Adding the logins for a hotel group from a spreadsheet, rather than one form at a time.

Seven hotels means seven managers plus whoever is in the office, and typing them in one by
one is both slow and where a wrong property code slips in unnoticed.

Two decisions worth knowing about.

**There is no password column.**  A file of plaintext passwords gets e-mailed, sits in
Downloads and eventually reaches a repository, and the first thing anyone does with one is
give everybody the same password.  Instead each imported account is created with a password
nobody knows, and the import hands back a single-use link per person for choosing their own.
Where mail is configured the link is sent to them; otherwise it is shown once, to the
administrator, to pass on.

**Nothing is written unless every row is good.**  A half-finished import leaves somebody
wondering which four of seven managers exist, so a single bad row stops the lot and says which
line it was on.  Fix the file, upload it again.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

#: The header the template writes, and what an uploaded file is read against.  Order does not
#: matter, case does not matter, and any column we do not know is ignored -- a spreadsheet
#: exported from somewhere else usually carries a few.
COLUMNS = ("username", "role", "properties", "email", "enabled")
REQUIRED = ("username", "role", "email")

ROLES = ("admin", "manager")
TRUE = ("yes", "y", "true", "1", "on", "enabled")
FALSE = ("no", "n", "false", "0", "off", "disabled")

USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


@dataclass
class Row:
    line: int
    username: str
    role: str
    properties: list[str]
    email: str
    enabled: bool
    exists: bool = False                 # an update rather than a new account

    @property
    def action(self) -> str:
        return "update" if self.exists else "create"


@dataclass
class Parsed:
    rows: list[Row] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.rows)


def template(properties: Iterable[str]) -> str:
    """A file to fill in, with this deployment's own property codes in the examples."""
    codes = list(properties)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    w.writerow(["okcon.gm", "manager", codes[0] if codes else "OKCON",
                "gm@example.com", "yes"])
    w.writerow(["area.manager", "manager", " ".join(codes[:2]) if len(codes) > 1 else "OKCON TXI47",
                "area@example.com", "yes"])
    w.writerow(["bookkeeper", "admin", "", "office@example.com", "yes"])
    return buf.getvalue()


def _split_properties(raw: str) -> list[str]:
    """Codes separated by whatever the person reached for: comma, space, semicolon, newline."""
    return [p for p in re.split(r"[,;\s]+", (raw or "").strip()) if p]


def parse(text: str, known_properties: Iterable[str], existing_usernames: Iterable[str],
          protect: Optional[str] = None) -> Parsed:
    """Read the file and check every row.  `protect` is the username doing the import.

    Nothing here writes anything; the caller applies `rows` only when there are no `errors`.
    """
    known = {str(p).strip().upper() for p in known_properties}
    existing = {str(u).strip() for u in existing_usernames}
    out = Parsed()

    text = text.lstrip("﻿")                   # Excel puts a byte-order mark on a CSV
    try:
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = [(f or "").strip().lower() for f in (reader.fieldnames or [])]
    except csv.Error as e:
        out.errors.append(f"That file could not be read as a CSV: {e}")
        return out
    if not fieldnames:
        out.errors.append("The file is empty. Download the template and fill it in.")
        return out
    missing = [c for c in REQUIRED if c not in fieldnames]
    if missing:
        out.errors.append(
            f"The header is missing {', '.join(missing)}. It needs at least "
            f"{', '.join(REQUIRED)}; properties and enabled are optional. "
            f"Found: {', '.join(fieldnames) or 'nothing'}.")
        return out

    seen: dict[str, int] = {}
    for n, raw in enumerate(reader, start=2):      # line 1 is the header
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.get(c) for c in COLUMNS):
            continue                               # a blank line, which every spreadsheet adds

        where = f"Line {n}"
        username = row.get("username", "")
        role = row.get("role", "").lower()
        email = row.get("email", "")
        props = _split_properties(row.get("properties", ""))
        enabled_raw = row.get("enabled", "").lower()

        if not USERNAME.match(username):
            out.errors.append(f"{where}: '{username}' is not a usable username. Letters, digits, "
                              "dot, dash and underscore, 2 to 64 characters, no spaces.")
            continue
        if username in seen:
            out.errors.append(f"{where}: '{username}' is already on line {seen[username]}.")
            continue
        seen[username] = n
        if protect and username == protect:
            out.errors.append(f"{where}: '{username}' is the account doing the import. Change "
                              "your own role, properties or password on your own account page, "
                              "so a typo here cannot lock you out.")
            continue
        if role not in ROLES:
            out.errors.append(f"{where}: role is '{role or 'blank'}', and it has to be "
                              f"{' or '.join(ROLES)}.")
            continue
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            out.errors.append(f"{where}: '{email or 'blank'}' is not an e-mail address. Every "
                              "account needs one -- it is how they set their own password.")
            continue
        if enabled_raw and enabled_raw not in TRUE + FALSE:
            out.errors.append(f"{where}: enabled is '{enabled_raw}'. Use yes or no.")
            continue

        bad = [p for p in props if p.upper() not in known]
        if bad:
            out.errors.append(f"{where}: no property here is called {', '.join(bad)}. "
                              f"The codes are: {', '.join(sorted(known)) or 'none yet'}.")
            continue
        if role == "manager" and not props:
            out.errors.append(f"{where}: a manager needs at least one property, or they can see "
                              "nothing. An admin sees every property and leaves this blank.")
            continue
        if role == "admin" and props:
            out.errors.append(f"{where}: an admin already sees every property, so leave the "
                              "properties column blank for {username}.".replace("{username}", username))
            continue

        out.rows.append(Row(line=n, username=username, role=role,
                            properties=[p.upper() for p in props], email=email,
                            enabled=enabled_raw not in FALSE, exists=username in existing))

    if not out.rows and not out.errors:
        out.errors.append("There were no rows to import, only a header.")
    return out
