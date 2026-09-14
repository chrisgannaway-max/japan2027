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
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from pms_to_odoo.export import entries_to_flat_csv, entries_to_odoo_csv
from pms_to_odoo.journal import format_entry, post_entry
from pms_to_odoo.odoo_client import OdooClient, OdooError, OdooSettings
from pms_to_odoo.pipeline import STATUS_LABELS, RunResult, load_properties, process_file

from .auth import SessionSigner, User, UserStore
from .db import Database

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = Path(os.environ.get("PORTAL_CONFIG", ROOT / "config" / "properties.yaml"))
USERS = Path(os.environ.get("PORTAL_USERS", ROOT / "config" / "users.yaml"))
DATA = Path(os.environ.get("PORTAL_DATA", ROOT / "data"))
ALLOWED_SUFFIXES = (".pdf", ".csv", ".xlsx", ".xlsm", ".eml", ".txt")

app = FastAPI(title="Night Audit to Odoo", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS


class State:
    def __init__(self):
        self.props = load_properties(CONFIG)
        users_path = USERS if USERS.exists() else USERS.with_name("users.example.yaml")
        self.users = UserStore(users_path)
        self.db = Database(DATA / "portal.db")
        self.signer = SessionSigner()
        self.odoo_enabled = bool(os.environ.get("ODOO_URL") and os.environ.get("ODOO_API_KEY"))


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
    state.props = load_properties(CONFIG)             # pick up mapping edits
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
