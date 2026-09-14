"""Command line entry point.

    python -m pms_to_odoo daily   --property HGI-EXAMPLE --file report.pdf [--date 2026-09-13] [--dry-run|--post]
    python -m pms_to_odoo daily   --property HGI-EXAMPLE --file gm_sheet.xlsx --from-excel   # GM spreadsheet
    python -m pms_to_odoo batch   --dir /inbox/night-audit [--dry-run]                       # many files
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
from .parsers import ExcelTemplateParser, get_parser

HERE = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = HERE / "config" / "properties.yaml"


def load_properties(path: Path) -> dict[str, dict]:
    if not path.exists():
        alt = path.with_name("properties.example.yaml")
        if alt.exists():
            print(f"[warn] {path.name} not found, using {alt.name}", file=sys.stderr)
            path = alt
        else:
            raise SystemExit(f"Config {path} not found")
    data = yaml.safe_load(path.read_text()) or {}
    props = {p["code"]: p for p in data.get("properties", [])}
    for p in props.values():
        p.setdefault("_config_dir", path.parent)
    return props


def _resolve(prop: dict, key: str) -> Optional[Path]:
    v = prop.get(key)
    if not v:
        return None
    p = Path(v)
    return p if p.is_absolute() else prop["_config_dir"] / p


def _parse_report(prop: dict, file: Path, business_date: Optional[date], from_excel: bool):
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
          f" (business date {report.business_date})")
    if args.verbose or args.dry_run:
        print("\n  Report line                               Amount        -> Account")
        for label, amt, status in mapping.coverage_report(report):
            print(f"  {label[:40]:<40} {amt:>14}  -> {status}")
        if report.stats:
            print("  statistics: " + ", ".join(f"{k}={v}" for k, v in report.stats.items()))
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


def cmd_batch(args) -> int:
    """Process every file in a folder.  File names must start with the property code:
    ``HGI-EXAMPLE_2026-09-13.pdf`` -> property HGI-EXAMPLE, date 2026-09-13 (date optional)."""
    props = load_properties(Path(args.config))
    folder = Path(args.dir)
    rc = 0
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in (".pdf", ".csv", ".xlsx", ".txt") or f.name.startswith("."):
            continue
        stem = f.stem
        code = next((c for c in sorted(props, key=len, reverse=True) if stem.upper().startswith(c.upper())), None)
        if not code:
            print(f"skip {f.name}: no property code prefix", file=sys.stderr)
            continue
        rest = stem[len(code):].strip("_- ")
        ns = argparse.Namespace(config=args.config, property=code, file=str(f), date=rest[:10] if rest[:4].isdigit() else None,
                                dry_run=args.dry_run, post=args.post, from_excel=False, verbose=False)
        print(f"\n=== {f.name} ===")
        try:
            rc |= cmd_daily(ns)
        except (OdooError, MappingError, ValueError) as e:
            print(f"ERROR {f.name}: {e}", file=sys.stderr)
            rc |= 2
        else:
            if args.done_dir and not args.dry_run:
                Path(args.done_dir).mkdir(parents=True, exist_ok=True)
                f.rename(Path(args.done_dir) / f.name)
    return rc


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
    b.set_defaults(func=cmd_batch)

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
