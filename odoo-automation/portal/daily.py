"""The morning list: what arrived, what is stuck, and who has not reported.

With packs arriving by e-mail and nobody signing in but the bookkeeper, this is the only thing
watching.  A GM who uploads sees within seconds whether their night balanced; a GM who e-mails
sees nothing at all, so a night can go missing in silence.  This is what someone at the office
picks up the phone from.

Four kinds of trouble, deliberately on one list rather than four screens, because they are all
the same job -- find out what happened to last night:

    never arrived        nothing at all for that property, and its cut-off has passed
    needs account codes  parsed, but a line has no GL account yet
    did not balance      parsed, but the numbers do not add up
    stuck                balanced, but Odoo has refused it often enough that it gave up

Everything else -- arrived, balanced, in Odoo -- is one line at the bottom, because a list that
shows the fine ones as prominently as the broken ones gets skimmed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

from pms_to_odoo.pipeline import STATUS_LABELS, RunResult

#: when a property is considered late if nothing has arrived (overridden per property by
#: `due_by: "05:30"` in its configuration).
DEFAULT_DUE_BY = time(6, 0)

TROUBLE = ("missing", "needs_codes", "unbalanced", "stuck", "problem")


@dataclass
class Row:
    code: str
    name: str
    state: str
    detail: str = ""
    run_id: Optional[int] = None

    @property
    def is_trouble(self) -> bool:
        return self.state in TROUBLE


@dataclass
class Report:
    day: date                      # the business date, i.e. the night being reported on
    rows: list[Row] = field(default_factory=list)

    @property
    def trouble(self) -> list[Row]:
        return [r for r in self.rows if r.is_trouble]

    @property
    def fine(self) -> list[Row]:
        return [r for r in self.rows if not r.is_trouble]

    @property
    def all_clear(self) -> bool:
        return not self.trouble


HEADINGS = {
    "missing": "Nothing has arrived",
    "needs_codes": "Waiting for account codes",
    "unbalanced": "Does not balance",
    "stuck": "Odoo would not take it",
    "problem": "Other problems",
    "waiting": "Not due yet",
    "posted": "In Odoo",
    "ready": "Balanced, on its way to Odoo",
}


def due_by(prop: dict) -> time:
    raw = str(prop.get("due_by") or "").strip()
    if raw:
        try:
            hh, _, mm = raw.partition(":")
            return time(int(hh), int(mm or 0))
        except ValueError:
            pass                       # a typo in the config must not stop the report going out
    return DEFAULT_DUE_BY


def build(db, props: dict, day: date, now: Optional[datetime] = None,
          max_attempts: int = 5) -> Report:
    """One row per property for `day`, the business date of the night in question."""
    now = now or datetime.now()
    runs = {r["property_code"]: r for r in db.runs_for_date(day.isoformat())}
    report = Report(day=day)

    for code, prop in sorted(props.items()):
        name = prop.get("name", code)
        r = runs.get(code)
        if r is None:
            late = now >= datetime.combine(day + timedelta(days=1), due_by(prop))
            report.rows.append(Row(code, name, "missing" if late else "waiting",
                                   f"due by {due_by(prop).strftime('%H:%M')}"))
            continue

        status, run_id = r["status"], r["id"]
        if status == "ok" and r["posted_at"]:
            report.rows.append(Row(code, name, "posted",
                                   f"Odoo move {r['odoo_move_id']}" if r["odoo_move_id"] else "", run_id))
        elif status == "ok" and (r["post_attempts"] or 0) >= max_attempts:
            report.rows.append(Row(code, name, "stuck", (r["post_error"] or "")[:200], run_id))
        elif status == "ok":
            report.rows.append(Row(code, name, "ready", "", run_id))
        elif status == "unmapped":
            res = RunResult.from_json(r["result_json"])
            labels = ", ".join(l["label"] for l in res.unmapped[:3]) if res.unmapped else ""
            more = f" and {len(res.unmapped) - 3} more" if len(res.unmapped) > 3 else ""
            report.rows.append(Row(code, name, "needs_codes", labels + more, run_id))
        elif status == "unbalanced":
            report.rows.append(Row(code, name, "unbalanced", r["message"] or "", run_id))
        else:
            report.rows.append(Row(code, name, "problem",
                                   f"{STATUS_LABELS.get(status, status)}: {r['message'] or ''}".strip(": "),
                                   run_id))
    return report


def as_text(report: Report, base_url: str = "") -> str:
    """Plain text, because this is read on a phone at 6am."""
    out = [f"Night audit for {report.day.isoformat()}", ""]
    if report.all_clear:
        out.append(f"All {len(report.rows)} properties reported and balanced. Nothing to do.")
        return "\n".join(out)

    out.append(f"{len(report.trouble)} of {len(report.rows)} properties need attention.")
    for state in TROUBLE:
        rows = [r for r in report.trouble if r.state == state]
        if not rows:
            continue
        out += ["", f"{HEADINGS[state]}:"]
        for r in rows:
            line = f"  {r.code} - {r.name}"
            if r.detail:
                line += f"\n      {r.detail}"
            if r.run_id and base_url:
                line += f"\n      {base_url}/runs/{r.run_id}"
            out.append(line)

    ok = [r for r in report.fine if r.state in ("posted", "ready")]
    if ok:
        out += ["", f"In Odoo or on the way: {', '.join(r.code for r in ok)}"]
    not_due = [r for r in report.fine if r.state == "waiting"]
    if not_due:
        out += ["", f"Not due yet: {', '.join(r.code for r in not_due)}"]
    return "\n".join(out)
