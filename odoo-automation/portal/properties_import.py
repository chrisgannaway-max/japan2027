"""Adding the hotels from a spreadsheet, rather than one form at a time.

A group's portfolio arrives as a list, and filling in the same form fifteen times is where a
wrong PMS or a mistyped report id slips in unnoticed -- neither of which announces itself until
a pack arrives at six in the morning and lands nowhere.

Same two rules as the login import next door: **nothing is written unless every row is good**,
so you are never left wondering which nine of fifteen hotels exist; and a code that already
exists is an **update**, which here means the GL mapping and anything the file does not mention
is left exactly as it was.

The file deliberately does not carry the GL mapping. A chart of accounts is a page of YAML per
hotel, it belongs on the property's own screen or in the worksheet import, and a spreadsheet
cell is the wrong shape for it.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Iterable

from pms_to_odoo.parsers import ALIASES, PARSERS

COLUMNS = ("code", "name", "brand", "pms", "pms_property_id", "pms_property_name",
           "company", "analytic", "journal", "due_by", "enabled")
REQUIRED = ("code", "pms")

CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$")
TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
TRUE = ("yes", "y", "true", "1", "on", "enabled")
FALSE = ("no", "n", "false", "0", "off", "disabled")


def known_pms() -> list[str]:
    return sorted(PARSERS)


def normalise_pms(raw: str) -> str:
    """'Hilton', 'choice advantage', 'opera-cloud' -> the key the parser registry uses."""
    key = (raw or "").upper().replace(" ", "").replace("-", "").replace("_", "")
    return ALIASES.get(key, key)


@dataclass
class Row:
    line: int
    code: str
    pms: str
    #: only the columns the file actually carried, so a narrow file updates only what it names
    fields: dict
    enabled: bool = True
    exists: bool = False

    def get(self, key: str, default: str = "") -> str:
        return self.fields.get(key, default)

    @property
    def action(self) -> str:
        return "update" if self.exists else "create"


@dataclass
class Parsed:
    rows: list[Row] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.rows)


def template() -> str:
    """A file to fill in, with one example per PMS we can read."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    w.writerow(["OKCON", "Embassy Suites by Hilton Oklahoma City Northwest", "Hilton", "PEP",
                "OKCON", "", "", "", "NA", "06:00", "yes"])
    w.writerow(["TXI47", "Comfort Inn Wichita Falls", "Choice", "CHOICEADV",
                "TXI47", "", "", "", "NA", "", "yes"])
    w.writerow(["CANDLEWOOD-MOORE", "Candlewood Suites Moore", "IHG", "OPERA",
                "", "Candlewood Suites Moore", "", "", "NA", "", "yes"])
    return buf.getvalue()


def parse(text: str, existing_codes: Iterable[str]) -> Parsed:
    """Read the file and check every row.  Nothing here writes anything."""
    existing = {str(c).strip().upper() for c in existing_codes}
    valid = set(known_pms())
    out = Parsed()

    text = text.lstrip("﻿")                   # Excel puts a byte-order mark on a CSV
    try:
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = [(f or "").strip().lower().replace(" ", "_") for f in (reader.fieldnames or [])]
    except csv.Error as e:
        out.errors.append(f"That file could not be read as a CSV: {e}")
        return out
    if not fieldnames:
        out.errors.append("The file is empty. Download the template and fill it in.")
        return out
    missing = [c for c in REQUIRED if c not in fieldnames]
    if missing:
        out.errors.append(
            f"The header is missing {', '.join(missing)}. It needs at least {', '.join(REQUIRED)}; "
            f"the rest are optional. Found: {', '.join(fieldnames) or 'nothing'}.")
        return out

    seen: dict[str, int] = {}
    for n, raw in enumerate(reader, start=2):      # line 1 is the header
        row = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip()
               for k, v in raw.items() if k is not None}
        if not any(row.get(c) for c in COLUMNS):
            continue                               # a blank line, which every spreadsheet adds

        where = f"Line {n}"
        code = row.get("code", "")
        pms = normalise_pms(row.get("pms", ""))
        due = row.get("due_by", "")
        enabled_raw = row.get("enabled", "").lower()

        if not CODE.match(code):
            out.errors.append(f"{where}: '{code}' is not a usable code. Letters, digits, dot, "
                              "dash and underscore, 2 to 32 characters, no spaces. It is what "
                              "goes in the journal reference, so keep it short.")
            continue
        if code.upper() in seen:
            out.errors.append(f"{where}: '{code}' is already on line {seen[code.upper()]}.")
            continue
        seen[code.upper()] = n
        if pms not in valid:
            out.errors.append(f"{where}: there is no reader for '{row.get('pms') or 'blank'}'. "
                              f"Use one of: {', '.join(known_pms())}. GENERIC reads a plain "
                              "table when nothing else fits.")
            continue
        if due and not TIME.match(due):
            out.errors.append(f"{where}: due_by is '{due}'. Use a 24-hour time like 06:00, or "
                              "leave it blank for the 06:00 default.")
            continue
        if enabled_raw and enabled_raw not in TRUE + FALSE:
            out.errors.append(f"{where}: enabled is '{enabled_raw}'. Use yes or no.")
            continue

        # Worth saying, not worth refusing: the hotel can still be chosen by hand on the
        # upload page, it just cannot be recognised from the report on its own.
        if not row.get("pms_property_id") and not row.get("pms_property_name"):
            out.warnings.append(
                f"{where}: {code} has neither an id nor a name as printed on the report, so a "
                "pack arriving by e-mail cannot be matched to it. Add one once you have seen a "
                "real report from this hotel.")

        # Only what the file carried.  A file of codes and names must leave the Odoo company,
        # the analytic account and the GL mapping exactly as they were -- passing a blank for
        # every column we know about would clear them all.
        fields = {k: row[k] for k in COLUMNS if k in row and k not in ("pms", "enabled")}
        fields["code"] = code.upper()
        fields["pms"] = pms
        enabled = enabled_raw not in FALSE
        if "enabled" in row:
            fields["enabled"] = "1" if enabled else "0"
        out.rows.append(Row(line=n, code=code.upper(), pms=pms, fields=fields, enabled=enabled,
                            exists=code.upper() in existing))

    if not out.rows and not out.errors:
        out.errors.append("There were no rows to import, only a header.")
    return out
