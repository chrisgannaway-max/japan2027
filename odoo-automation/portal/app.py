"""FastAPI application.

    uvicorn portal.app:app --host 0.0.0.0 --port 8000        (or: python -m portal serve)

Environment:
    PORTAL_CONFIG   config/properties.yaml (default: config/properties.yaml or the example)
    PORTAL_USERS    config/users.yaml
    PORTAL_DATA     data/  (uploads + sqlite)
    PORTAL_SECRET   cookie signing secret (set one in production)
    ODOO_*          optional; enables "Post to Odoo" on the review screen
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from pms_to_odoo.export import bills_to_odoo_csv, entries_to_flat_csv, entries_to_odoo_csv
from pms_to_odoo.invoices import InvoiceData, InvoiceLine, create_vendor_bill, extract_invoice_auto, reader_in_use
from pms_to_odoo.invoices.to_odoo import load_expense_map
from pms_to_odoo.invoices.accounts import AccountAssigner
from pms_to_odoo.journal import format_entry, post_entry
from pms_to_odoo.odoo_client import OdooClient, OdooError, OdooSettings
from pms_to_odoo.mapping import GLMapping
from pms_to_odoo.mapping_edit import insert_rules, rule_for, suggest_account
from pms_to_odoo.invoices.sniff import looks_like_invoice
from pms_to_odoo.parsers import detect_pms as _detect_pms, get_parser, read_text as _read_text
from pms_to_odoo.pipeline import STATUS_LABELS, RunResult, load_properties, process_file, resolve

from .auth import SessionSigner, User, UserStore, hash_password
from .db import Database
from .store import ConfigStore, PROPERTY_COLUMNS, read_mapping_text, store_mode, write_mapping_text

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = Path(os.environ.get("PORTAL_CONFIG", ROOT / "config" / "properties.yaml"))
USERS = Path(os.environ.get("PORTAL_USERS", ROOT / "config" / "users.yaml"))
DATA = Path(os.environ.get("PORTAL_DATA", ROOT / "data"))
ALLOWED_SUFFIXES = (".pdf", ".csv", ".xlsx", ".xlsm", ".eml", ".txt")
INVOICE_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".txt")
VENDOR_TEMPLATES = ROOT / "config" / "vendor_templates.yaml"
EXPENSE_MAP = ROOT / "config" / "expense_categories.yaml"
VENDOR_ACCOUNTS = ROOT / "config" / "vendor_accounts.yaml"

app = FastAPI(title="Night Audit to Odoo", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS


class State:
    def __init__(self):
        self.mode = store_mode()                                   # "yaml" | "db"
        self.delivery = os.environ.get("DELIVERY_MODE", "download").lower()   # "download" | "odoo"
        self.db = Database(DATA / "portal.db")
        self.store = ConfigStore(DATA / "portal.db", DATA) if self.mode == "db" else None
        self.signer = SessionSigner()
        self.odoo_enabled = bool(os.environ.get("ODOO_URL") and os.environ.get("ODOO_API_KEY"))
        self.reload_config()

    def reload_config(self) -> None:
        if self.store is not None:
            self.props = self.store.properties()
            self.users = UserStore(records=self.store.users())
        else:
            self.props = load_properties(CONFIG)
            users_path = USERS if USERS.exists() else USERS.with_name("users.example.yaml")
            self.users = UserStore(users_path)


state = State()


# ------------------------------------------------------------------ auth helpers
def current_user(request: Request) -> Optional[User]:
    username = state.signer.verify(request.cookies.get("session"))
    return state.users.get(username) if username else None


def require_user(request: Request) -> User:
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return u


def require_admin(request: Request) -> User:
    u = require_user(request)
    if not u.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return u


def visible_properties(u: User) -> dict[str, dict]:
    return state.props if u.is_admin else {c: p for c, p in state.props.items() if c in u.properties}


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("user", current_user(request))
    ctx.setdefault("odoo_enabled", state.odoo_enabled)
    ctx.setdefault("invoice_reader", reader_in_use())
    ctx.setdefault("store_mode", state.mode)
    ctx.setdefault("delivery", state.delivery)
    return templates.TemplateResponse(request, name, ctx)


@app.exception_handler(HTTPException)
async def redirect_on_303(request: Request, exc: HTTPException):
    if exc.status_code == 303:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code)


# ------------------------------------------------------------------ login
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html", error=None)


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    u = state.users.authenticate(username.strip(), password)
    if not u:
        return render(request, "login.html", error="Wrong username or password.")
    resp = RedirectResponse("/" if u.is_admin else "/upload", status_code=303)
    resp.set_cookie("session", state.signer.sign(u.username), httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


# ------------------------------------------------------------------ manager: upload
@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, user: User = Depends(require_user)):
    props = visible_properties(user)
    runs = state.db.recent_runs(20, None if user.is_admin else list(props))
    return render(request, "upload.html", props=props, runs=runs, results=None)


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, user: User = Depends(require_user),
                 property_code: str = Form(""), files: list[UploadFile] = File(...)):
    props = visible_properties(user)
    allowed = None if user.is_admin else set(props)
    if property_code and property_code not in props:
        raise HTTPException(403, "Property not allowed")
    results: list[tuple[int, RunResult]] = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = DATA / "uploads" / (property_code or "unsorted") / stamp
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for f in files:
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(f.filename or "upload").name)
        if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
            results.append((0, RunResult(status="error", file_name=name, message="Only PDF, CSV, XLSX, EML or TXT files")))
            continue
        target = folder / name
        target.write_bytes(await f.read())
        saved.append(target)
    consumed: set[Path] = set()
    for path in saved:                       # same folder, so SynXis pairs find each other
        if path in consumed:
            continue
        res = process_file(path, state.props, property_code or None, allowed)
        consumed.update(Path(c) for c in res.companions)
        run_id = state.db.add_run(uploaded_by=user.username, property_code=res.property_code,
                                  business_date=res.business_date.isoformat() if res.business_date else None,
                                  pms=res.pms, ref=res.ref, status=res.status, message=res.message,
                                  file_name=path.name, stored_path=str(path), result_json=res.to_json())
        results.append((run_id, res))
    runs = state.db.recent_runs(20, None if user.is_admin else list(props))
    return render(request, "upload.html", props=props, runs=runs, results=results)


# ------------------------------------------------------------------ admin: dashboard
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, day: Optional[str] = None, user: User = Depends(require_user)):
    if not user.is_admin:
        return RedirectResponse("/upload", status_code=303)
    d = date.fromisoformat(day) if day else date.today() - timedelta(days=1)
    runs = state.db.runs_for_date(d.isoformat())
    latest: dict[str, object] = {}
    for r in runs:
        latest.setdefault(r["property_code"], r)
    rows = []
    for code, p in state.props.items():
        r = latest.get(code)
        rows.append({"code": code, "name": p.get("name", code), "pms": p.get("pms", ""), "run": r,
                     "result": RunResult.from_json(r["result_json"]) if r else None})
    ready = [r for r in runs if r["status"] == "ok"]
    return render(request, "dashboard.html", day=d, rows=rows, ready=ready,
                  prev_day=(d - timedelta(days=1)).isoformat(), next_day=(d + timedelta(days=1)).isoformat(),
                  dates=state.db.dates_with_runs())


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: int, user: User = Depends(require_user)):
    r = state.db.get_run(run_id)
    if not r:
        raise HTTPException(404)
    if not user.is_admin and r["property_code"] not in user.properties:
        raise HTTPException(403)
    res = RunResult.from_json(r["result_json"])
    return render(request, "run.html", run=r, res=res,
                  entry_text=format_entry(res.entry) if res.entry else None)


@app.post("/runs/{run_id}/approve")
def approve(run_id: int, user: User = Depends(require_admin)):
    r = state.db.get_run(run_id)
    if not r or r["status"] != "ok":
        raise HTTPException(400, "Only balanced runs can be approved")
    state.db.mark(run_id, approved_at=datetime.now().isoformat(timespec="seconds"), approved_by=user.username)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/post")
def post_to_odoo(run_id: int, user: User = Depends(require_admin), post_now: str = Form("no")):
    r = state.db.get_run(run_id)
    if not r or r["status"] != "ok":
        raise HTTPException(400, "Only balanced runs can be posted")
    if not state.odoo_enabled:
        raise HTTPException(400, "Odoo connection not configured (ODOO_URL / ODOO_API_KEY)")
    res = RunResult.from_json(r["result_json"])
    try:
        client = OdooClient.connect(OdooSettings.from_env())
        result = post_entry(res.entry, client, post=(post_now == "yes"))
    except OdooError as e:
        raise HTTPException(502, f"Odoo error: {e}") from e
    state.db.mark(run_id, posted_at=datetime.now().isoformat(timespec="seconds"), odoo_move_id=result.move_id)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/reprocess")
def reprocess(run_id: int, user: User = Depends(require_admin)):
    r = state.db.get_run(run_id)
    if not r:
        raise HTTPException(404)
    state.reload_config()                            # pick up mapping edits
    res = process_file(Path(r["stored_path"]), state.props, r["property_code"] or None)
    new_id = state.db.add_run(uploaded_by=user.username, property_code=res.property_code,
                              business_date=res.business_date.isoformat() if res.business_date else None,
                              pms=res.pms, ref=res.ref, status=res.status, message=res.message,
                              file_name=r["file_name"], stored_path=r["stored_path"], result_json=res.to_json())
    return RedirectResponse(f"/runs/{new_id}", status_code=303)


# ------------------------------------------------------------------ admin: export
def _entries_for(day: str, only_approved: bool) -> list:
    out = []
    for r in state.db.runs_for_date(day):
        if r["status"] != "ok" or (only_approved and not r["approved_at"]):
            continue
        res = RunResult.from_json(r["result_json"])
        if res.entry:
            out.append((r["id"], res.entry))
    return out


@app.get("/export/{day}.csv")
def export_day(day: str, fmt: str = "odoo", approved: str = "all", user: User = Depends(require_admin)):
    date.fromisoformat(day)
    pairs = _entries_for(day, approved == "yes")
    entries = [e for _, e in pairs]
    body = entries_to_odoo_csv(entries) if fmt == "odoo" else entries_to_flat_csv(entries)
    for run_id, _ in pairs:
        state.db.mark(run_id, exported_at=datetime.now().isoformat(timespec="seconds"))
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="night-audit-{day}-{fmt}.csv"'})


@app.get("/runs/{run_id}.csv")
def export_run(run_id: int, fmt: str = "odoo", user: User = Depends(require_admin)):
    r = state.db.get_run(run_id)
    if not r or r["status"] != "ok":
        raise HTTPException(404)
    res = RunResult.from_json(r["result_json"])
    body = entries_to_odoo_csv([res.entry]) if fmt == "odoo" else entries_to_flat_csv([res.entry])
    state.db.mark(run_id, exported_at=datetime.now().isoformat(timespec="seconds"))
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{res.ref}-{fmt}.csv"'})


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"


# ------------------------------------------------------------------ invoices
def _expense_categories() -> dict[str, str]:
    path = EXPENSE_MAP if EXPENSE_MAP.exists() else EXPENSE_MAP.with_name("expense_categories.example.yaml")
    return load_expense_map(path)


def _assigner() -> AccountAssigner:
    path = VENDOR_ACCOUNTS if VENDOR_ACCOUNTS.exists() else VENDOR_ACCOUNTS.with_name("vendor_accounts.example.yaml")
    return AccountAssigner(path, state.db.remembered_account)


def _invoice_status(fields: dict, inv_id: int = 0) -> tuple[str, str]:
    """ready when the bill can go straight into the export; otherwise needs_review + why."""
    problems = []
    if not fields.get("vendor_name"):
        problems.append("vendor missing")
    if not fields.get("invoice_number"):
        problems.append("invoice number missing")
    if not fields.get("invoice_date"):
        problems.append("invoice date missing")
    try:
        if Decimal(str(fields.get("total") or 0)) <= 0:
            problems.append("total missing")
    except Exception:  # noqa: BLE001
        problems.append("total not a number")
    if not fields.get("account_code"):
        problems.append("no expense account could be assigned")
    if state.db.find_duplicate_invoice(fields.get("vendor_name", ""), fields.get("invoice_number", ""), inv_id):
        problems.append("possible duplicate")
    return ("ready", "") if not problems else ("needs_review", "; ".join(problems))


def _invoice_form_ctx(request: Request, user: User, **extra):
    props = visible_properties(user)
    invoices = state.db.list_invoices(None, None if user.is_admin else list(props), 30)
    return dict(props=props, invoices=invoices, categories=_expense_categories(), **extra)


@app.get("/invoices", response_class=HTMLResponse)
def invoices_page(request: Request, user: User = Depends(require_user)):
    return render(request, "invoices.html", **_invoice_form_ctx(request, user, draft=None, inv_id=None))


def _create_invoice(path: Path, property_code: str, username: str, file_name: str) -> int:
    """Read one invoice file and store it as a draft. Shared by the invoice page and the
    'this is actually an invoice' button on the night-audit page."""
    data, reader = extract_invoice_auto(path, VENDOR_TEMPLATES)
    description = data.lines[0].description if data.lines and data.lines[0].description != "Invoice total" else ""
    account, how = _assigner().assign(data.vendor_name, description or data.review_notes)
    fields = dict(property_code=property_code, vendor_name=data.vendor_name, vendor_tax_id=data.vendor_tax_id or "",
                  invoice_number=data.invoice_number, invoice_date=data.invoice_date, due_date=data.due_date or "",
                  subtotal=str(data.dec("subtotal")), tax_amount=str(data.dec("tax_amount")), total=str(data.dec("total")),
                  account_code=account or "", description=description or f"Invoice {data.invoice_number}".strip(),
                  notes=f"account by {how}; {data.review_notes}".strip("; "))
    status, why = _invoice_status(fields)
    if why:
        fields["notes"] = f"{why}; {fields['notes']}"
    inv_id = state.db.add_invoice(uploaded_by=username, file_name=file_name, stored_path=str(path), reader=reader,
                                  confidence=data.confidence, **fields)
    state.db.update_invoice(inv_id, status=status)
    return inv_id


def _night_audit_report_in(path: Path) -> Optional[str]:
    """The PMS name if this file is a night-audit report, else None. Never raises."""
    if path.suffix.lower() not in (".pdf", ".txt", ".csv", ".eml", ".xlsx", ".xlsm"):
        return None
    try:
        return _detect_pms(_read_text(path))
    except Exception:  # noqa: BLE001 - an unreadable file is simply not a report
        return None


@app.post("/invoices/upload", response_class=HTMLResponse)
async def invoice_upload(request: Request, user: User = Depends(require_user),
                         property_code: str = Form(""), file: UploadFile = File(...)):
    props = visible_properties(user)
    if property_code and property_code not in props:
        raise HTTPException(403, "Property not allowed")
    if not property_code and len(props) == 1:
        property_code = next(iter(props))
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(file.filename or "invoice").name)
    if Path(name).suffix.lower() not in INVOICE_SUFFIXES:
        return render(request, "invoices.html", **_invoice_form_ctx(request, user, draft=None, inv_id=None,
                      error="Only PDF, PNG, JPG or TXT invoices"))
    folder = DATA / "invoices" / (property_code or "unsorted") / datetime.now().strftime("%Y%m")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{name}"
    target.write_bytes(await file.read())
    pms = _night_audit_report_in(target)
    if pms:
        target.unlink(missing_ok=True)
        return render(request, "invoices.html", **_invoice_form_ctx(request, user, draft=None, inv_id=None,
                      error=f"That is a {pms} night-audit report, not an invoice. Upload it on the Night audit page."))
    inv_id = _create_invoice(target, property_code, user.username, name)
    return RedirectResponse(f"/invoices/{inv_id}", status_code=303)


@app.get("/invoices/{inv_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, inv_id: int, user: User = Depends(require_user)):
    inv = state.db.get_invoice(inv_id)
    if not inv:
        raise HTTPException(404)
    if not user.is_admin and inv["property_code"] not in user.properties:
        raise HTTPException(403)
    dup = state.db.find_duplicate_invoice(inv["vendor_name"], inv["invoice_number"], inv_id)
    return render(request, "invoices.html", **_invoice_form_ctx(request, user, draft=inv, inv_id=inv_id, duplicate=dup))


@app.post("/invoices/{inv_id}", response_class=HTMLResponse)
async def invoice_save(request: Request, inv_id: int, user: User = Depends(require_user)):
    inv = state.db.get_invoice(inv_id)
    if not inv:
        raise HTTPException(404)
    if not user.is_admin and inv["property_code"] not in user.properties:
        raise HTTPException(403)
    form = await request.form()
    fields = {k: (form.get(k) or "").strip() for k in Database.INVOICE_FIELDS}
    if not user.is_admin and fields["property_code"] not in user.properties:
        raise HTTPException(403)
    for k in ("subtotal", "tax_amount", "total"):
        v = fields[k].replace("$", "").replace(",", "")
        fields[k] = str(Decimal(v)) if v else "0"
    if not fields["subtotal"] or fields["subtotal"] == "0":
        fields["subtotal"] = str(Decimal(fields["total"]) - Decimal(fields["tax_amount"]))
    action = form.get("action", "save")
    if not fields["account_code"]:
        fields["account_code"] = _assigner().assign(fields["vendor_name"], fields["description"])[0] or ""
    status, why = _invoice_status(fields, inv_id)
    if action == "hold" and user.is_admin:
        status = "held"
    elif action == "reject" and user.is_admin:
        status = "rejected"
    elif inv["status"] in ("exported", "posted"):
        status = inv["status"]                      # already out the door: edits do not un-export
    elif action == "release" and user.is_admin:
        status = "ready" if not why or why == "possible duplicate" else "needs_review"
    if why and status == "needs_review":
        fields["notes"] = why
    state.db.update_invoice(inv_id, status=status, **fields)
    return RedirectResponse(f"/invoices/{inv_id}", status_code=303)


@app.get("/invoices/export/bills.csv")
def invoices_export(status: str = "ready", user: User = Depends(require_admin)):
    """Default: every ready bill not yet exported. Exporting remembers each vendor's account."""
    if status == "ready":
        rows = [r for r in state.db.list_invoices("ready", None, 1000)]
    elif status == "all":
        rows = state.db.list_invoices(None, None, 1000)
    else:
        rows = state.db.list_invoices(status, None, 1000)
    body = bills_to_odoo_csv([dict(r) for r in rows])
    now = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        if r["status"] == "ready":
            state.db.update_invoice(r["id"], exported_at=now, status="exported")
            state.db.remember_account(r["vendor_name"], r["account_code"])
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="vendor-bills-{datetime.now():%Y%m%d-%H%M}.csv"'})


@app.post("/invoices/{inv_id}/post")
def invoice_post(inv_id: int, user: User = Depends(require_admin)):
    inv = state.db.get_invoice(inv_id)
    if not inv or inv["status"] not in ("ready", "exported"):
        raise HTTPException(400, "Bill is not ready (needs review, held or rejected)")
    if not state.odoo_enabled:
        raise HTTPException(400, "Odoo connection not configured (ODOO_URL / ODOO_API_KEY)")
    data = InvoiceData(vendor_name=inv["vendor_name"], vendor_tax_id=inv["vendor_tax_id"] or None,
                       invoice_number=inv["invoice_number"], invoice_date=inv["invoice_date"], due_date=inv["due_date"] or None,
                       subtotal=inv["subtotal"], tax_amount=inv["tax_amount"], total=inv["total"],
                       lines=[InvoiceLine(description=inv["description"] or f"Invoice {inv['invoice_number']}", quantity="1",
                                          unit_price=inv["subtotal"], amount=inv["subtotal"], category_hint="")],
                       confidence=inv["confidence"] or "medium", review_notes=inv["notes"] or "")
    prop = state.props.get(inv["property_code"], {})
    try:
        client = OdooClient.connect(OdooSettings.from_env())
        res = create_vendor_bill(data, client, inv["stored_path"], company=prop.get("company"),
                                 default_account=inv["account_code"] or None, create_missing_vendor=True)
    except OdooError as e:
        raise HTTPException(502, f"Odoo error: {e}") from e
    if res.move_id:
        state.db.update_invoice(inv_id, status="posted", posted_at=datetime.now().isoformat(timespec="seconds"),
                                odoo_move_id=res.move_id)
        state.db.remember_account(inv["vendor_name"], inv["account_code"])
    return RedirectResponse(f"/invoices/{inv_id}", status_code=303)


# ------------------------------------------------------------------ dashboard: send a whole day to Odoo
@app.post("/export/{day}/post")
def post_day(day: str, post_now: str = Form("no"), user: User = Depends(require_admin)):
    if not state.odoo_enabled:
        raise HTTPException(400, "Odoo connection not configured (ODOO_URL / ODOO_API_KEY)")
    client = OdooClient.connect(OdooSettings.from_env())
    for r in state.db.runs_for_date(day):
        if r["status"] != "ok" or r["posted_at"]:
            continue
        res = RunResult.from_json(r["result_json"])
        try:
            result = post_entry(res.entry, client, post=(post_now == "yes"))
        except OdooError as e:
            raise HTTPException(502, f"Odoo error on {res.ref}: {e}") from e
        state.db.mark(r["id"], posted_at=datetime.now().isoformat(timespec="seconds"), odoo_move_id=result.move_id)
    return RedirectResponse(f"/?day={day}", status_code=303)


# ------------------------------------------------------------------ admin: configuration (db store)
def require_db_store():
    if state.store is None:
        raise HTTPException(400, "Configuration is read from files (PORTAL_STORE=yaml). Set PORTAL_STORE=db to edit it here.")
    return state.store


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, user: User = Depends(require_admin), msg: str = "", err: str = ""):
    ctx = dict(msg=msg, err=err, config_path=str(CONFIG), users_path=str(USERS),
               accounts_count=state.db.accounts_info()["count"])
    if state.store is not None:
        ctx["properties"] = [state.store.get_property(c) | {"enabled": state.store.get_property(c)["enabled"]} for c in
                             state.store.properties(include_disabled=True)]
        ctx["users_list"] = state.store.all_users()
    else:
        ctx["properties"] = [{k: p.get(k, "") for k in PROPERTY_COLUMNS if k != "mapping_yaml"} | {"enabled": 1} for p in state.props.values()]
        ctx["users_list"] = [{"username": u.username, "role": u.role, "properties": ",".join(u.properties), "enabled": 1}
                             for u in state.users.users.values()]
    return render(request, "admin.html", **ctx)


@app.post("/admin/import-yaml")
def admin_import(user: User = Depends(require_admin), overwrite: str = Form("no")):
    store = require_db_store()
    counts = store.import_from_yaml(CONFIG, USERS, overwrite=(overwrite == "yes"))
    state.reload_config()
    return RedirectResponse(f"/admin?msg=Imported+{counts['properties']}+properties+and+{counts['users']}+users", status_code=303)


@app.get("/admin/properties/{code}", response_class=HTMLResponse)
def admin_property_form(request: Request, code: str, user: User = Depends(require_admin)):
    store = require_db_store()
    prop = store.get_property(code) if code != "new" else {k: "" for k in PROPERTY_COLUMNS} | {"enabled": 1}
    if prop is None:
        raise HTTPException(404)
    return render(request, "admin_property.html", prop=prop, is_new=(code == "new"), err="")


@app.post("/admin/properties/{code}", response_class=HTMLResponse)
async def admin_property_save(request: Request, code: str, user: User = Depends(require_admin)):
    store = require_db_store()
    form = await request.form()
    if form.get("action") == "delete" and code != "new":
        store.delete_property(code)
        state.reload_config()
        return RedirectResponse("/admin?msg=Property+deleted", status_code=303)
    fields = {k: (form.get(k) or "") for k in PROPERTY_COLUMNS}
    fields["enabled"] = "1" if form.get("enabled") else "0"
    try:
        store.save_property(**fields)
    except ValueError as e:
        return render(request, "admin_property.html", prop=fields, is_new=(code == "new"), err=str(e))
    state.reload_config()
    return RedirectResponse(f"/admin?msg=Saved+{fields['code']}", status_code=303)


@app.post("/admin/users")
async def admin_user_save(request: Request, user: User = Depends(require_admin)):
    store = require_db_store()
    form = await request.form()
    try:
        if form.get("action") == "delete":
            store.delete_user(form.get("username", ""))
        else:
            props = [p.strip() for p in (form.get("properties") or "").replace(";", ",").split(",")]
            store.save_user(form.get("username", ""), form.get("role", "manager"), props,
                            password=(form.get("password") or None), enabled=bool(form.get("enabled", "1")))
    except ValueError as e:
        return RedirectResponse(f"/admin?err={e}", status_code=303)
    state.reload_config()
    return RedirectResponse("/admin?msg=User+saved", status_code=303)


# ------------------------------------------------------------------ chart of accounts
def _mapping_loader(prop: dict):
    """GLMapping for a property, however its mapping is stored (used for suggestions)."""
    import tempfile
    text = read_mapping_text(prop, state.store)
    if not text.strip():
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(text); tmp = fh.name
    try:
        return GLMapping.load(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _accounts() -> list:
    return state.db.list_accounts()


@app.get("/admin/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, user: User = Depends(require_admin), q: str = "", msg: str = "", err: str = ""):
    return render(request, "admin_accounts.html", accounts=state.db.list_accounts(q, 500),
                  info=state.db.accounts_info(), q=q, msg=msg, err=err)


@app.post("/admin/accounts/upload")
async def accounts_upload(request: Request, user: User = Depends(require_admin), file: UploadFile = File(...)):
    import csv as _csv
    import io as _io
    raw = (await file.read()).decode("utf-8-sig", "replace")
    rows = list(_csv.DictReader(_io.StringIO(raw)))
    if not rows:
        return RedirectResponse("/admin/accounts?err=That+file+has+no+rows", status_code=303)
    keys = {k.strip().lower(): k for k in rows[0]}
    def pick(*names):
        for n in names:
            if n in keys: return keys[n]
        return None
    kc, kn, kt = pick("code", "account code", "id"), pick("name", "account name", "label"), pick("type", "account_type", "account type")
    if not kc:
        return RedirectResponse("/admin/accounts?err=No+'code'+column+found", status_code=303)
    n = state.db.replace_accounts([{"code": r.get(kc), "name": r.get(kn) if kn else "",
                                    "account_type": r.get(kt) if kt else ""} for r in rows], f"upload: {file.filename}")
    return RedirectResponse(f"/admin/accounts?msg=Loaded+{n}+accounts", status_code=303)


@app.post("/admin/accounts/pull")
def accounts_pull(user: User = Depends(require_admin), company: str = Form("")):
    if not state.odoo_enabled:
        return RedirectResponse("/admin/accounts?err=Odoo+connection+not+configured", status_code=303)
    try:
        client = OdooClient.connect(OdooSettings.from_env())
        rows = client.chart_of_accounts(client.company_id(company or None))
    except OdooError as e:
        return RedirectResponse(f"/admin/accounts?err=Odoo+error:+{e}", status_code=303)
    n = state.db.replace_accounts(rows, "pulled from Odoo")
    return RedirectResponse(f"/admin/accounts?msg=Pulled+{n}+accounts+from+Odoo", status_code=303)


# ------------------------------------------------------------------ needs mapping
@app.get("/admin/mapping/{run_id}", response_class=HTMLResponse)
def mapping_page(request: Request, run_id: int, user: User = Depends(require_admin), err: str = ""):
    r = state.db.get_run(run_id)
    if not r:
        raise HTTPException(404)
    res = RunResult.from_json(r["result_json"])
    prop = state.props.get(res.property_code)
    if prop is None:
        raise HTTPException(400, f"Property {res.property_code} is not configured")
    rows = []
    for line in res.unmapped:
        sug = suggest_account(line, res.pms, state.props, _mapping_loader, exclude=res.property_code)
        rows.append({**line, "suggested": sug.account, "suggested_from": sug.source})
    return render(request, "admin_mapping.html", run=r, res=res, rows=rows, accounts=_accounts(), err=err)


@app.post("/admin/mapping/{run_id}")
async def mapping_save(request: Request, run_id: int, user: User = Depends(require_admin)):
    r = state.db.get_run(run_id)
    if not r:
        raise HTTPException(404)
    res = RunResult.from_json(r["result_json"])
    prop = state.props.get(res.property_code)
    if prop is None:
        raise HTTPException(400, f"Property {res.property_code} is not configured")
    form = await request.form()
    new_rules, ignored = [], 0
    for i, line in enumerate(res.unmapped):
        choice = (form.get(f"account_{i}") or "").strip()
        if not choice:
            continue
        if choice == "__ignore__":
            ignored += 1
            continue
        rule = rule_for(line["label"], line.get("code", ""), line.get("section", ""), choice)
        if rule not in new_rules:          # the same label can appear on several report lines
            new_rules.append(rule)
    if not new_rules and not ignored:
        return RedirectResponse(f"/admin/mapping/{run_id}?err=Nothing+chosen", status_code=303)
    text = read_mapping_text(prop, state.store)
    if ignored:                       # "not an accounting line" -> an ignore pattern, not a rule
        from pms_to_odoo.mapping_edit import escape_label, yq
        pats = [f"  - {yq('^' + escape_label(l['label']) + '$')}"
                for i, l in enumerate(res.unmapped) if (form.get(f"account_{i}") or "") == "__ignore__"]
        if re.search(r"^ignore:", text, re.M):
            text = re.sub(r"^ignore:.*$", "ignore:\n" + "\n".join(pats), text, count=1, flags=re.M)
        else:
            text = text.rstrip("\n") + "\nignore:\n" + "\n".join(pats) + "\n"
    text = insert_rules(text, new_rules, f"# added from the portal {date.today().isoformat()} by {user.username}")
    try:
        where = write_mapping_text(prop, state.store, text)
    except ValueError as e:
        return RedirectResponse(f"/admin/mapping/{run_id}?err={e}", status_code=303)
    state.reload_config()
    res2 = process_file(Path(r["stored_path"]), state.props, res.property_code)
    new_id = state.db.add_run(uploaded_by=f"{user.username} (remapped)", property_code=res2.property_code,
                              business_date=res2.business_date.isoformat() if res2.business_date else None,
                              pms=res2.pms, ref=res2.ref, status=res2.status, message=res2.message,
                              file_name=r["file_name"], stored_path=r["stored_path"], result_json=res2.to_json())
    print(f"[mapping] {len(new_rules)} rule(s), {ignored} ignore(s) for {res.property_code} -> {where}")
    return RedirectResponse(f"/runs/{new_id}" if res2.status == "ok" else f"/admin/mapping/{new_id}", status_code=303)


# ------------------------------------------------------------------ worksheet import
def _pms_from_sheet(name: str) -> Optional[str]:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w]
    for w in reversed(words):
        try:
            return get_parser(w).pms
        except KeyError:
            continue
    return None


@app.post("/admin/mapping/import/worksheet", response_class=HTMLResponse)
async def import_worksheet(request: Request, user: User = Depends(require_admin), file: UploadFile = File(...)):
    import io as _io
    import openpyxl
    try:
        wb = openpyxl.load_workbook(_io.BytesIO(await file.read()), data_only=True)
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(f"/admin?err=Could+not+read+that+workbook:+{e}", status_code=303)
    applied, skipped = [], []
    for sheet in wb.worksheets:
        pms = _pms_from_sheet(sheet.title)
        if pms is None:
            skipped.append(f"{sheet.title}: not a PMS tab")
            continue
        head = {str(c.value).strip().lower(): c.column for c in sheet[4] if c.value}
        need = ("section", "pms code", "line as printed on the report", "your account code")
        if not all(k in head for k in need):
            skipped.append(f"{sheet.title}: unexpected columns")
            continue
        rules = []
        for row in range(5, sheet.max_row + 1):
            acct = sheet.cell(row, head["your account code"]).value
            label = sheet.cell(row, head["line as printed on the report"]).value
            if not acct or not label:
                continue
            rules.append(rule_for(str(label), str(sheet.cell(row, head["pms code"]).value or "").strip(),
                                  str(sheet.cell(row, head["section"]).value or "").strip(), str(acct).strip()))
        if not rules:
            skipped.append(f"{sheet.title}: no account codes filled in")
            continue
        targets = [c for c, p in state.props.items() if str(p.get("pms", "")).upper() == pms]
        if not targets:
            skipped.append(f"{sheet.title}: no property uses {pms}")
            continue
        for code in targets:
            prop = state.props[code]
            text = insert_rules(read_mapping_text(prop, state.store), rules,
                                f"# imported from the mapping worksheet {date.today().isoformat()} by {user.username}")
            try:
                write_mapping_text(prop, state.store, text)
                applied.append(f"{code}: {len(rules)} rules")
            except ValueError as e:
                skipped.append(f"{code}: {e}")
    state.reload_config()
    return render(request, "admin_import_result.html", applied=applied, skipped=skipped)


@app.post("/runs/{run_id}/to-invoice")
def run_to_invoice(run_id: int, user: User = Depends(require_user)):
    """The night-audit page decided this file is an invoice; move it across."""
    r = state.db.get_run(run_id)
    if not r:
        raise HTTPException(404)
    if not user.is_admin and r["uploaded_by"] != user.username:
        raise HTTPException(403)
    if r["status"] == "moved_to_invoice":
        raise HTTPException(400, "That file has already been moved to Invoices")
    res = RunResult.from_json(r["result_json"])
    if res.status != "looks_like_invoice":
        raise HTTPException(400, "That upload is not waiting to be moved")
    prop = res.property_code or (user.properties[0] if len(user.properties) == 1 else "")
    inv_id = _create_invoice(Path(r["stored_path"]), prop, user.username, r["file_name"])
    state.db.mark(run_id, superseded=1, status="moved_to_invoice",
                  message=f"moved to invoice #{inv_id} by {user.username}")
    return RedirectResponse(f"/invoices/{inv_id}", status_code=303)
