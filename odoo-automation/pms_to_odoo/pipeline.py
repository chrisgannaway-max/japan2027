"""One code path from "a file arrived" to "a balanced journal entry (or a clear reason why not)".

Used by the CLI (`batch`), the web portal (uploads) and any future intake.  Nothing here
talks to Odoo; posting is a separate, explicit step.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml

from .mapping import GLMapping, MappingError
from .models import DailyReport, JournalEntry, JournalLine
from .invoices.sniff import looks_like_invoice
from .parsers import ExcelTemplateParser, detect_pms, get_parser, read_text

STATUS_LABELS = {
    "ok": "Balanced, ready to post",
    "unmapped": "Needs mapping",
    "unbalanced": "Does not balance",
    "awaiting_companion": "Waiting for the other half of the report",
    "unrecognised": "Not a report we book",
    "looks_like_invoice": "Looks like an invoice",
    "moved_to_invoice": "Moved to invoices",
    "wrong_pms": "Report is from a different PMS",
    "unknown_property": "Property not recognised",
    "not_allowed": "Property not allowed for this user",
    "error": "Error",
}


@dataclass
class RunResult:
    status: str                       # key of STATUS_LABELS
    message: str = ""
    file_name: str = ""
    property_code: str = ""
    pms: str = ""
    business_date: Optional[date] = None
    report_id: str = ""
    property_name: str = ""
    ref: str = ""
    entry: Optional[JournalEntry] = None
    coverage: list[tuple[str, str, str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Decimal] = field(default_factory=dict)
    companions: list[str] = field(default_factory=list)
    unmapped: list[dict] = field(default_factory=list)   # {label, code, section, amount} needing an account

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    # ---- (de)serialisation for the portal database ---------------------------
    def to_json(self) -> str:
        d = {
            "status": self.status, "message": self.message, "file_name": self.file_name,
            "property_code": self.property_code, "pms": self.pms,
            "business_date": self.business_date.isoformat() if self.business_date else None,
            "report_id": self.report_id, "property_name": self.property_name, "ref": self.ref,
            "coverage": self.coverage, "warnings": self.warnings,
            "stats": {k: str(v) for k, v in self.stats.items()}, "companions": self.companions,
            "unmapped": self.unmapped,
            "entry": None,
        }
        if self.entry:
            d["entry"] = {
                "ref": self.entry.ref, "date": self.entry.date.isoformat(), "journal_code": self.entry.journal_code,
                "narration": self.entry.narration, "company_code": self.entry.company_code,
                "lines": [{"account_code": l.account_code, "name": l.name, "debit": str(l.debit), "credit": str(l.credit),
                           "partner_ref": l.partner_ref, "analytic_code": l.analytic_code} for l in self.entry.lines],
            }
        return json.dumps(d)

    @classmethod
    def from_json(cls, raw: str) -> "RunResult":
        d = json.loads(raw)
        r = cls(status=d["status"], message=d.get("message", ""), file_name=d.get("file_name", ""),
                property_code=d.get("property_code", ""), pms=d.get("pms", ""),
                business_date=date.fromisoformat(d["business_date"]) if d.get("business_date") else None,
                report_id=d.get("report_id", ""), property_name=d.get("property_name", ""), ref=d.get("ref", ""),
                coverage=[tuple(c) for c in d.get("coverage", [])], warnings=d.get("warnings", []),
                stats={k: Decimal(v) for k, v in d.get("stats", {}).items()}, companions=d.get("companions", []),
                unmapped=d.get("unmapped", []))
        e = d.get("entry")
        if e:
            r.entry = JournalEntry(ref=e["ref"], date=date.fromisoformat(e["date"]), journal_code=e["journal_code"],
                                   narration=e["narration"], company_code=e.get("company_code"),
                                   lines=[JournalLine(l["account_code"], l["name"], Decimal(l["debit"]), Decimal(l["credit"]),
                                                      l.get("partner_ref"), l.get("analytic_code")) for l in e["lines"]])
        return r


# ------------------------------------------------------------------ configuration helpers
def load_properties(path: Path) -> dict[str, dict]:
    """properties.yaml -> {code: property dict}; falls back to properties.example.yaml."""
    if not path.exists():
        alt = path.with_name("properties.example.yaml")
        if not alt.exists():
            raise FileNotFoundError(f"Config {path} not found")
        path = alt
    data = yaml.safe_load(path.read_text()) or {}
    props = {p["code"]: p for p in data.get("properties", [])}
    for p in props.values():
        p["_config_dir"] = path.parent
    return props


def resolve(prop: dict, key: str) -> Optional[Path]:
    v = prop.get(key)
    if not v:
        return None
    p = Path(v)
    return p if p.is_absolute() else prop["_config_dir"] / p


def match_property(props: dict[str, dict], file: Path, report_id: str, report_name: str,
                   pms: Optional[str], allowed: Optional[set[str]] = None) -> Optional[str]:
    """Property code from the report's own id/name, else the file-name prefix, else the only
    property using that PMS.  ``allowed`` restricts the candidates (a manager's properties)."""
    cands = {c: p for c, p in props.items() if allowed is None or c in allowed}
    for code, p in cands.items():
        if report_id and str(p.get("pms_property_id", "")).upper() == report_id.upper():
            return code
    if report_name:
        # Name matching must be unambiguous.  Several of these hotels sit at the same airport,
        # and one configured name can be a substring of another property's report ("... Airport"
        # inside "... Airport West").  Two matches means we do not know which hotel this is, and
        # guessing posts one hotel's night into another's books, silently.  Hold instead.
        hits = [code for code, p in cands.items()
                if str(p.get("pms_property_name", "")).lower()
                and str(p.get("pms_property_name", "")).lower() in report_name.lower()]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    stem = file.stem.upper()
    for code in sorted(cands, key=len, reverse=True):
        if stem.startswith(code.upper()):
            return code
    if pms:
        same = [c for c, p in cands.items() if str(p.get("pms", "")).upper() == pms]
        if len(same) == 1:
            return same[0]
    if allowed is not None and len(cands) == 1:
        only = next(iter(cands))
        if pms is None or str(cands[only].get("pms", "")).upper() == pms:
            return only
    return None


# ------------------------------------------------------------------ the pipeline
def parse_for_property(prop: dict, file: Path, business_date: Optional[date] = None,
                       from_excel: bool = False) -> DailyReport:
    if from_excel or (file.suffix.lower() in (".xlsx", ".xlsm") and prop.get("excel_template")):
        cell_map = resolve(prop, "excel_template")
        if not cell_map:
            raise ValueError(f"{prop['code']}: excel_template not configured")
        return ExcelTemplateParser(cell_map).parse(file, prop["code"], business_date)
    return get_parser(prop["pms"]).parse(file, prop["code"], business_date)


def process_file(file: Path, props: dict[str, dict], property_code: Optional[str] = None,
                 allowed: Optional[set[str]] = None, business_date: Optional[date] = None) -> RunResult:
    """Detect, parse, map and balance one report file.  Never raises for content problems."""
    res = RunResult(status="error", file_name=file.name)
    try:
        text = read_text(file)
    except Exception as e:  # noqa: BLE001
        res.message = f"Could not read file: {e}"
        return res
    pms = detect_pms(text)
    maybe_invoice = looks_like_invoice(text) if pms is None else False
    probe = None
    if pms:
        probe = get_parser(pms).parse(file, property_code or "?", business_date)
        res.pms, res.report_id, res.property_name = pms, probe.pms_property_id, probe.property_name
        res.business_date, res.companions = probe.business_date, probe.companions
        if not probe.recognised:
            res.status, res.message = "unrecognised", "; ".join(probe.warnings) or "Not the report this parser books."
            return res
    code = property_code or match_property(props, file, res.report_id, res.property_name, pms, allowed)
    if not code or code not in props:
        if not pms:
            if maybe_invoice:
                res.status = "looks_like_invoice"
                res.message = "This is not a night-audit report, but it does look like a vendor invoice."
            else:
                res.status, res.message = "unrecognised", "Could not tell which PMS produced this file."
        else:
            res.status = "unknown_property"
            res.message = (f"Report id {res.report_id!r} / name {res.property_name!r} does not match a configured property"
                           + (" you have access to" if allowed is not None else "") + ".")
        return res
    if allowed is not None and code not in allowed:
        res.status, res.message = "not_allowed", f"You are not allowed to upload for {code}."
        return res
    prop = props[code]
    res.property_code = code
    if pms and str(prop.get("pms", "")).upper() != pms:
        res.status = "wrong_pms"
        res.message = (f"This file is a {pms} report, but {code} uses {prop.get('pms')}. "
                       f"Pick the right property or upload the {prop.get('pms')} report.")
        return res
    try:
        report = probe if (probe is not None and probe.property_code == code) else parse_for_property(prop, file, business_date)
        if probe is not None and probe.property_code != code:
            report.property_code = code
        res.pms, res.business_date, res.report_id = report.pms, report.business_date, report.pms_property_id
        res.property_name, res.warnings, res.stats = report.property_name, res.warnings + report.warnings, report.stats
        res.companions = report.companions
        if not report.recognised or not report.lines:
            # an invoice reaches here too when the manager picked a property first, so the
            # "this is an invoice" answer has to win over the parser's own complaint
            if maybe_invoice:
                res.status = "looks_like_invoice"
                res.message = "This is not a night-audit report, but it does look like a vendor invoice."
            elif not report.recognised:
                res.status = "unrecognised"
                res.message = "; ".join(report.warnings) or "Not the report this parser books."
            else:
                res.status = "unrecognised"
                res.message = f"No report lines recognised by the {report.pms} parser; is this the right report for {code}?"
            return res
        res.ref = f"{report.pms}-{report.property_code}-{report.business_date.isoformat()}"
        if report.awaiting_companion:
            # Half a report is not a broken report.  Saying "does not balance" here would send
            # somebody looking for an error that is really just a file that has not arrived yet.
            res.status = "awaiting_companion"
            res.message = f"{report.awaiting_companion} has not arrived yet for this night."
            return res
        mapping = GLMapping.load(resolve(prop, "gl_mapping"))
        res.coverage = mapping.coverage_report(report)
        try:
            res.entry = mapping.build_entry(report)
        except MappingError as e:
            res.status = "unbalanced" if "does not balance" in str(e) else "unmapped"
            res.message = str(e)
            res.unmapped = [{"label": l.label, "code": l.code, "section": l.section, "amount": str(l.amount)}
                            for l in mapping.unmapped_lines(report)]
            return res
        res.status = "ok"
        res.message = f"{len(res.entry.lines)} journal lines, total {res.entry.total_debit:,.2f}"
    except Exception as e:  # noqa: BLE001
        res.status, res.message = "error", f"{type(e).__name__}: {e}"
    return res
