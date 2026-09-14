"""Dump the layout-preserving text of a PDF (what the parsers see) to stdout or a file.

    python tools/extract_layout_text.py report.pdf [out.txt] [--redact "Name One" --redact "Name Two"]

Use it to build test fixtures from real reports and to see why a line did not parse.
Redaction replaces the given strings with "REDACTED" so staff names don't end up in git.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pms_to_odoo.parsers.base import read_text  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--redact", action="append", default=[])
    ap.add_argument("--pages", help="comma separated page numbers to keep, e.g. 5,6,7,11")
    a = ap.parse_args()
    text = read_text(a.src)
    if a.pages:
        keep = {int(x) for x in a.pages.split(",")}
        chunks, cur, out = text.split("===== PAGE "), None, []
        for ch in chunks[1:]:
            num = int(ch.split(" ", 1)[0])
            if num in keep:
                out.append("===== PAGE " + ch)
        text = "".join(out)
    for r in a.redact:
        text = text.replace(r, "REDACTED".ljust(len(r)))
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out} ({len(text)} chars)")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
