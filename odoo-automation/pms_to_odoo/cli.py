"""Command line entry point.

    python -m pms_to_odoo daily   --property HGI-EXAMPLE --file report.pdf [--date 2026-09-13] [--dry-run|--post]
    python -m pms_to_odoo daily   --property HGI-EXAMPLE --file gm_sheet.xlsx --from-excel   # GM spreadsheet
    python -m pms_to_odoo batch   --dir /inbox/night-audit [--dry-run]                       # many files (.pdf/.eml)
    python -m pms_to_odoo inspect --file report.pdf                                           # show what a report parses to
    python -m pms_to_odoo invoice --file invoice.pdf [--company "Example Hotel LLC"] [--dry-run] [--create-vendor]
    python -m pms_to_odoo accounts [--company ...]      # dump Odoo chart of accounts (to build mappings)
    python -m pms_to_odoo check                          # verify Odoo connection + mapping files

Property definitions live in config/properties.yaml (see properties.example.yaml).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

from .journal import format_entry, post_entry
from .mapping import GLMapping, MappingError
from .odoo_client import OdooClient, OdooError, OdooSettings
from .parsers import ExcelTemplateParser, detect_pms, get_parser, read_text
from .parsers.base import eml_attachments
from .pipeline import load_properties as _load_props, resolve as _resolve  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = HERE / "config" / "properties.yaml"


def load_properties(path: Path) -> dict[str, dict]:
    try:
        props = _load_props(path)
    except FileNotFoundError as e:
        raise SystemExit(str(e)) from e
    if not path.exists():
        print(f"[warn] {path.name} not found, using properties.example.yaml", file=sys.stderr)
    return props


def _parse_report(prop: dict, file: Path, business_date: Optional[date], from_excel: bool):
    if file.suffix.lower() == ".eml":
        atts = eml_attachments(file)
        if not atts:
            raise SystemExit(f"{file.name}: no PDF attachment in email")
        if len(atts) > 1:
            print(f"[warn] {file.name}: {len(atts)} attachments, using {atts[0].name}", file=sys.stderr)
        file = atts[0]
    if from_excel or file.suffix.lower() in (".xlsx", ".xlsm") and prop.get("excel_template"):
        cell_map = _resolve(prop, "excel_template")
        if not cell_map:
            raise SystemExit(f"{prop['code']}: excel_template not configured")
        parser = ExcelTemplateParser(cell_map)
    else:
        parser = get_parser(prop["pms"])
    return parser.parse(file, prop["code"], business_date)


def _connect() -> OdooClient:
    return OdooClient.connect(OdooSettings.from_env())


def _append_log(log_path: Path, row: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    new = not log_path.exists()
    with log_path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


# ------------------------------------------------------------------ commands
def cmd_daily(args) -> int:
    props = load_properties(Path(args.config))
    prop = props.get(args.property)
    if not prop:
        raise SystemExit(f"Unknown property {args.property!r}. Known: {', '.join(props)}")
    bdate = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    report = _parse_report(prop, Path(args.file), bdate, args.from_excel)
    mapping = GLMapping.load(_resolve(prop, "gl_mapping"))

    print(f"Parsed {len(report.lines)} lines, {len(report.stats)} statistics from {report.source_file}"
          f" (business date {report.business_date}, report id {report.pms_property_id or '-'})")
    for w in report.warnings:
        print(f"[warn] {w}")
    if not report.recognised:
        print(f"SKIPPED: {Path(args.file).name} is not the report this parser books "
              f"({get_parser(prop['pms']).expects}).", file=sys.stderr)
        return 3
    if args.verbose or args.dry_run:
        print(f"\n  {'Report line':<48} {'section':<10} {'Amount':>14}   -> Account")
        for label, section, amt, status in mapping.coverage_report(report):
            if amt in ("0.00", "-0.00") and not args.verbose:
                continue
            print(f"  {label[:48]:<48} {section:<10} {amt:>14}   -> {status}")
        print()
    try:
        entry = mapping.build_entry(report)
    except MappingError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(format_entry(entry))
    if args.dry_run:
        print("\n(dry run - nothing sent to Odoo)")
        return 0
    client = _connect()
    result = post_entry(entry, client, post=args.post)
    print(f"\n{result.status.upper()}: {result.ref} -> account.move id {result.move_id} {result.message}")
    _append_log(HERE / "logs" / "daily_entries.csv", {
        "run_at": datetime.now().isoformat(timespec="seconds"), "property": prop["code"],
        "business_date": report.business_date.isoformat(), "ref": result.ref,
        "status": result.status, "move_id": result.move_id or "", "file": str(args.file),
        "debit_total": str(entry.total_debit),
    })
    return 0


def _expand_inputs(folder: Path) -> list[tuple[Path, Path]]:
    """(file to parse, original file) - .eml files are expanded to their PDF attachments."""
    out = []
    for f in sorted(folder.iterdir()):
        if f.name.startswith(".") or not f.is_file():
            continue
        if f.suffix.lower() == ".eml":
            for att in eml_attachments(f):
                out.append((att, f))
        elif f.suffix.lower() in (".pdf", ".csv", ".xlsx", ".txt"):
            out.append((f, f))
    return out


def cmd_batch(args) -> int:
    """Process every report in a folder through the shared pipeline.

    The property is recognised from the report itself (PEP Hotel ID, choiceADVANTAGE
    Property Code, HotelKey header, SynXis hotel id) via ``pms_property_id`` in
    properties.yaml, or from a file-name prefix like ``OKCON_2025-11-10.pdf``.  ``.eml``
    files are opened and their attachments processed; SynXis pairs are merged.
    """
    from .pipeline import process_file

    props = load_properties(Path(args.config))
    rc = 0
    consumed: set[str] = set()
    for f, original in _expand_inputs(Path(args.dir)):
        if str(f.resolve()) in consumed:
            print(f"\n=== {original.name} === merged into a previous report (companion file)")
            continue
        print(f"\n=== {original.name}" + (f" -> {f.name}" if f != original else "") + " ===")
        res = process_file(f, props)
        consumed.update(str(Path(c).resolve()) for c in res.companions)
        for w in res.warnings:
            print(f"[warn] {w}")
        if res.status != "ok":
            print(f"{res.label.upper()}: {res.message}", file=sys.stderr)
            rc |= 2 if res.status in ("unmapped", "unbalanced", "error", "unknown_property") else 0
            continue
        print(f"{res.property_code} {res.business_date}: {res.message}")
        if args.verbose or args.dry_run:
            print(format_entry(res.entry))
        if args.dry_run:
            continue
        client = _connect()
        result = post_entry(res.entry, client, post=args.post)
        print(f"{result.status.upper()}: {result.ref} -> account.move id {result.move_id} {result.message}")
        _append_log(HERE / "logs" / "daily_entries.csv", {
            "run_at": datetime.now().isoformat(timespec="seconds"), "property": res.property_code,
            "business_date": res.business_date.isoformat(), "ref": result.ref, "status": result.status,
            "move_id": result.move_id or "", "file": str(original), "debit_total": str(res.entry.total_debit)})
        if args.done_dir:
            Path(args.done_dir).mkdir(parents=True, exist_ok=True)
            original.rename(Path(args.done_dir) / original.name)
    return rc


def cmd_inspect(args) -> int:
    """Parse one file (auto-detecting the PMS) and print every line: the fastest way to
    see which labels a mapping file has to cover."""
    text = read_text(args.file)
    pms = args.pms or detect_pms(text)
    if not pms:
        print("Could not detect the PMS from the report text; pass --pms.", file=sys.stderr)
        return 2
    report = get_parser(pms).parse(args.file, args.property or "?", None)
    print(f"pms={report.pms} property_id={report.pms_property_id!r} name={report.property_name!r} "
          f"business_date={report.business_date} lines={len(report.lines)}")
    for w in report.warnings:
        print(f"[warn] {w}")
    print(f"\n  {'section':<10} {'code':<7} {'label':<48} {'amount':>14}")
    for l in report.lines:
        if l.amount == 0 and not args.zeros:
            continue
        print(f"  {l.section:<10} {l.code:<7} {l.label[:48]:<48} {l.amount:>14,.2f}")
    print()
    for sec in ("revenue", "tax", "settlement", "expense", "ledger", "transfer"):
        t = report.total(sec)
        if t:
            print(f"  total {sec:<10} {t:>14,.2f}")
    chk = report.total("revenue") + report.total("tax") + report.total("expense") - report.total("settlement") - report.total("ledger")
    print(f"  revenue + tax + expense - settlements - ledger change = {chk:,.2f}   (0.00 means the entry will balance)")
    if report.stats:
        print("\n  statistics / control totals:")
        for k, v in report.stats.items():
            print(f"    {k[:60]:<60} {v:>14,.2f}")
    return 0


def cmd_invoice(args) -> int:
    from .invoices import create_vendor_bill, extract_invoice
    from .invoices.to_odoo import load_expense_map

    inv = extract_invoice(args.file)
    print(json.dumps(inv.model_dump(), indent=2))
    if args.dry_run:
        print("\n(dry run - nothing sent to Odoo)")
        return 0
    client = _connect()
    expense_map = load_expense_map(args.expense_map or HERE / "config" / "expense_categories.yaml")
    res = create_vendor_bill(inv, client, args.file, company=args.company, expense_map=expense_map,
                             default_account=args.default_account, journal_code=args.journal,
                             create_missing_vendor=args.create_vendor)
    print(f"\n{res.status.upper()}: {res.partner_name} #{inv.invoice_number} -> account.move id {res.move_id} {res.message}")
    _append_log(HERE / "logs" / "invoices.csv", {
        "run_at": datetime.now().isoformat(timespec="seconds"), "vendor": res.partner_name,
        "invoice_number": inv.invoice_number, "total": inv.total, "status": res.status,
        "move_id": res.move_id or "", "confidence": inv.confidence, "file": str(args.file),
    })
    return 0 if res.status in ("created", "exists") else 3


def cmd_accounts(args) -> int:
    client = _connect()
    rows = client.chart_of_accounts(client.company_id(args.company))
    w = csv.writer(sys.stdout)
    w.writerow(["code", "name", "type"])
    for r in rows:
        w.writerow([r["code"], r["name"], r.get("account_type", "")])
    return 0


def cmd_check(args) -> int:
    ok = True
    props = load_properties(Path(args.config))
    for code, p in props.items():
        try:
            m = GLMapping.load(_resolve(p, "gl_mapping"))
            print(f"[ok]   {code}: pms={p['pms']} journal={m.journal} rules={len(m.rules)}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[FAIL] {code}: {e}")
    try:
        client = _connect()
        n = len(client.search_read("account.journal", [], ["code"], limit=5))
        print(f"[ok]   Odoo connection: read {n} journal(s)")
    except OdooError as e:
        ok = False
        print(f"[FAIL] Odoo connection: {e}")
    return 0 if ok else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="pms_to_odoo", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="properties.yaml path")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daily", help="post one night-audit report")
    d.add_argument("--property", required=True)
    d.add_argument("--file", required=True)
    d.add_argument("--date", help="business date YYYY-MM-DD (else read from the report)")
    d.add_argument("--from-excel", action="store_true", help="input is the GM spreadsheet template")
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("--post", action="store_true", help="post immediately instead of leaving draft")
    d.add_argument("-v", "--verbose", action="store_true")
    d.set_defaults(func=cmd_daily)

    b = sub.add_parser("batch", help="process a folder of reports")
    b.add_argument("--dir", required=True)
    b.add_argument("--done-dir", help="move processed files here")
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--post", action="store_true")
    b.add_argument("-v", "--verbose", action="store_true")
    b.set_defaults(func=cmd_batch)

    x = sub.add_parser("inspect", help="parse a report (PMS auto-detected) and list every line")
    x.add_argument("--file", required=True)
    x.add_argument("--pms", help="force a parser: PEP, CHOICEADV, HOTELKEY, OPERA, ...")
    x.add_argument("--property", help="property code to stamp on the output")
    x.add_argument("--zeros", action="store_true", help="also show zero-amount lines")
    x.set_defaults(func=cmd_inspect)

    i = sub.add_parser("invoice", help="extract a vendor invoice and create a draft bill")
    i.add_argument("--file", required=True)
    i.add_argument("--company")
    i.add_argument("--journal", help="purchase journal code (default: Odoo default)")
    i.add_argument("--default-account", help="expense account code when no category matches")
    i.add_argument("--expense-map", help="YAML mapping category_hint -> account code")
    i.add_argument("--create-vendor", action="store_true")
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(func=cmd_invoice)

    a = sub.add_parser("accounts", help="print chart of accounts as CSV")
    a.add_argument("--company")
    a.set_defaults(func=cmd_accounts)

    c = sub.add_parser("check", help="validate config and Odoo connection")
    c.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except OdooError as e:
        print(f"ODOO ERROR: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
