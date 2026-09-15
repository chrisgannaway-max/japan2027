"""Add rules to a property's mapping file without disturbing what is already there.

New rules are inserted at the TOP of the `rules:` block, because the first matching rule
wins and a hand-written catch-all near the bottom would otherwise swallow them.  The rest
of the file, comments included, is left byte-for-byte alone.

Rules are written as YAML flow mappings with single-quoted scalars, so a regex never has to
be backslash-escaped for YAML:

    - {code: 'RM', account: '4000'}                       # when the report prints a code
    - {match: '^GUEST ROOM$', account: '4000', section: revenue}   # otherwise, exact label
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from .mapping import GLMapping
from .models import ReportLine


def yq(value: str) -> str:
    """Single-quoted YAML scalar (only ' needs doubling; no backslash processing)."""
    return "'" + str(value).replace("'", "''") + "'"


#: characters that must be escaped inside a regex; re.escape() also escapes spaces, which is
#: valid but makes the mapping file unpleasant to read, so we escape only what matters.
_SPECIAL = re.compile(r"([\\.^$*+?{}\[\]()|])")


def escape_label(label: str) -> str:
    """Regex-escape a report label, leaving spaces and punctuation like % & / alone."""
    return _SPECIAL.sub(r"\\\1", label.strip())


def rule_for(label: str, code: str, section: str, account: str, side: str = "") -> str:
    """One `- {...}` line that matches exactly this report line and nothing else."""
    parts = []
    if code:
        parts.append(f"code: {yq(code)}")
    else:
        parts.append(f"match: {yq('^' + escape_label(label) + '$')}")
    parts.append(f"account: {yq(account)}")
    if section:
        parts.append(f"section: {section}")
    if side:
        parts.append(f"side: {side}")
    return "  - {" + ", ".join(parts) + "}"


def insert_rules(yaml_text: str, rule_lines: Iterable[str], note: Optional[str] = None) -> str:
    """Insert rule lines at the top of the `rules:` block, creating the block if absent."""
    rule_lines = [r for r in rule_lines if r.strip()]
    if not rule_lines:
        return yaml_text
    stamp = note or f"# added from the portal {date.today().isoformat()}"
    block = [f"  {stamp}"] + list(rule_lines)
    lines = yaml_text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^rules:\s*(\[\s*\])?\s*$", line):
            if line.strip().endswith("[]"):          # `rules: []` -> a real list
                lines[i] = "rules:"
            return "\n".join(lines[: i + 1] + block + lines[i + 1:]) + "\n"
    tail = "" if yaml_text.endswith("\n") else "\n"
    return yaml_text + tail + "rules:\n" + "\n".join(block) + "\n"


@dataclass
class Suggestion:
    account: str
    source: str        # where it came from, for the screen

    def __bool__(self) -> bool:
        return bool(self.account)


def suggest_account(line: dict, pms: str, properties: dict[str, dict], mapping_loader,
                    exclude: str = "") -> Suggestion:
    """Would another property's mapping already know this line?  Same PMS wins.

    `mapping_loader(prop)` returns a GLMapping (or None); it is passed in so the portal can
    read mappings from the database or from files without this module caring which.
    """
    probe = ReportLine(label=line.get("label", ""), amount=1, section=line.get("section", ""),
                       code=line.get("code", ""))
    same_pms, other = [], []
    for code, prop in properties.items():
        if code == exclude:
            continue
        (same_pms if str(prop.get("pms", "")).upper() == (pms or "").upper() else other).append((code, prop))
    for group, why in ((same_pms, "same PMS"), (other, "another property")):
        for code, prop in group:
            try:
                gl: Optional[GLMapping] = mapping_loader(prop)
            except Exception:  # noqa: BLE001 - a broken mapping elsewhere must not block this screen
                continue
            if gl is None:
                continue
            rule = gl.find_rule(probe)
            if rule:
                return Suggestion(rule.account, f"{code} ({why})")
    return Suggestion("", "")
