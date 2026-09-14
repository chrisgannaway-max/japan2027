"""Minimal client for Odoo's external API.

Two transports, same interface:

* ``Json2Transport``  - Odoo 19+.  POST https://host/json/2/<model>/<method>
                        with ``Authorization: bearer <api key>`` (and optionally
                        ``X-Odoo-Database``).  Recommended for new deployments.
* ``XmlRpcTransport`` - Odoo <= 18 (still works on 19 but is deprecated there).
                        /xmlrpc/2/common ``authenticate`` then /xmlrpc/2/object
                        ``execute_kw``.  An API key is passed in place of the password.

Nothing here depends on third-party packages so the connector can run anywhere
Python 3.10+ is available (a GM laptop, a small VM, a scheduled job).

Note from the Odoo docs: external API access on Odoo Online requires a *Custom*
pricing plan (not "One App Free" / "Standard").  On-premise / Odoo.sh have no such
restriction.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
import xmlrpc.client
from dataclasses import dataclass
from typing import Any, Optional


class OdooError(RuntimeError):
    """Raised for any error returned by the Odoo server."""


# --------------------------------------------------------------------------- transports
class Json2Transport:
    def __init__(self, url: str, api_key: str, database: Optional[str] = None,
                 user_agent: str = "pms-to-odoo", timeout: int = 60):
        self.base = url.rstrip("/") + "/json/2"
        self.api_key = api_key
        self.database = database
        self.user_agent = user_agent
        self.timeout = timeout
        self._ctx = ssl.create_default_context()

    def call(self, model: str, method: str, ids: Optional[list[int]] = None,
             context: Optional[dict] = None, **kwargs: Any) -> Any:
        body: dict[str, Any] = dict(kwargs)
        if ids:
            body["ids"] = ids
        if context:
            body["context"] = context
        data = json.dumps(body, default=str).encode("utf-8")
        headers = {
            "Authorization": f"bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": self.user_agent,
        }
        if self.database:
            headers["X-Odoo-Database"] = self.database
        req = urllib.request.Request(f"{self.base}/{model}/{method}", data=data,
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read())
                msg = f"{err.get('name')}: {err.get('message')}"
            except Exception:  # noqa: BLE001 - best effort error decoding
                msg = f"HTTP {e.code} {e.reason}"
            raise OdooError(f"{model}.{method} failed: {msg}") from e


class XmlRpcTransport:
    def __init__(self, url: str, database: str, username: str, password_or_api_key: str):
        self.url = url.rstrip("/")
        self.db = database
        self.username = username
        self.secret = password_or_api_key
        self._common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        self.uid = self._common.authenticate(self.db, self.username, self.secret, {})
        if not self.uid:
            raise OdooError("XML-RPC authentication failed (check database, login and API key)")

    def call(self, model: str, method: str, ids: Optional[list[int]] = None,
             context: Optional[dict] = None, **kwargs: Any) -> Any:
        args: list[Any] = [ids] if ids else []
        kw = dict(kwargs)
        if context:
            kw["context"] = context
        try:
            return self._models.execute_kw(self.db, self.uid, self.secret, model, method, args, kw)
        except xmlrpc.client.Fault as e:
            raise OdooError(f"{model}.{method} failed: {e.faultString.splitlines()[-1]}") from e


# --------------------------------------------------------------------------- high level
@dataclass
class OdooSettings:
    url: str
    database: Optional[str]
    api_key: str
    transport: str = "json2"          # "json2" (Odoo 19+) or "xmlrpc" (Odoo <= 18)
    username: Optional[str] = None    # only needed for xmlrpc

    @classmethod
    def from_env(cls) -> "OdooSettings":
        """Read ODOO_URL, ODOO_DB, ODOO_API_KEY, ODOO_TRANSPORT, ODOO_USER from the environment."""
        url = os.environ.get("ODOO_URL")
        key = os.environ.get("ODOO_API_KEY")
        if not url or not key:
            raise OdooError("Set ODOO_URL and ODOO_API_KEY (see README - Configuration)")
        return cls(url=url, database=os.environ.get("ODOO_DB"), api_key=key,
                   transport=os.environ.get("ODOO_TRANSPORT", "json2"),
                   username=os.environ.get("ODOO_USER"))


class OdooClient:
    """Convenience wrapper: lookups, idempotent creation of journal entries and bills."""

    def __init__(self, transport):
        self.t = transport
        self._cache: dict[tuple, Any] = {}

    @classmethod
    def connect(cls, s: OdooSettings) -> "OdooClient":
        if s.transport == "xmlrpc":
            if not (s.database and s.username):
                raise OdooError("xmlrpc transport needs ODOO_DB and ODOO_USER")
            return cls(XmlRpcTransport(s.url, s.database, s.username, s.api_key))
        return cls(Json2Transport(s.url, s.api_key, s.database))

    # ---- generic ORM helpers -------------------------------------------------------
    def search_read(self, model: str, domain: list, fields: list[str], limit: int = 0,
                    context: Optional[dict] = None, order: Optional[str] = None) -> list[dict]:
        kw: dict[str, Any] = {"domain": domain, "fields": fields}
        if limit:
            kw["limit"] = limit
        if order:
            kw["order"] = order
        return self.t.call(model, "search_read", context=context, **kw)

    def create(self, model: str, values: dict, context: Optional[dict] = None) -> int:
        res = self.t.call(model, "create", vals_list=[values], context=context)
        # JSON-2 returns a list of ids for a list of vals; XML-RPC returns the same when
        # given a list.  Normalise to a single int.
        return res[0] if isinstance(res, list) else res

    def write(self, model: str, ids: list[int], values: dict) -> bool:
        return self.t.call(model, "write", ids=ids, vals=values)

    def call_method(self, model: str, method: str, ids: list[int], **kwargs) -> Any:
        return self.t.call(model, method, ids=ids, **kwargs)

    # ---- master data lookups (cached per run) --------------------------------------
    def _lookup(self, key: tuple, model: str, domain: list, fields: list[str],
                context: Optional[dict] = None) -> Optional[dict]:
        if key in self._cache:
            return self._cache[key]
        rows = self.search_read(model, domain, fields, limit=1, context=context)
        self._cache[key] = rows[0] if rows else None
        return self._cache[key]

    def company_id(self, company_code_or_name: Optional[str]) -> Optional[int]:
        if not company_code_or_name:
            return None
        row = self._lookup(("company", company_code_or_name), "res.company",
                           ["|", ("name", "=", company_code_or_name),
                            ("name", "ilike", company_code_or_name)], ["id", "name"])
        if not row:
            raise OdooError(f"Company '{company_code_or_name}' not found in Odoo")
        return row["id"]

    def account_id(self, code: str, company_id: Optional[int] = None) -> int:
        ctx = {"allowed_company_ids": [company_id]} if company_id else None
        row = self._lookup(("account", code, company_id), "account.account",
                           [("code", "=", code)], ["id", "code", "name"], context=ctx)
        if not row:
            raise OdooError(f"GL account with code '{code}' not found in Odoo"
                            + (f" (company {company_id})" if company_id else ""))
        return row["id"]

    def journal_id(self, code: str, company_id: Optional[int] = None) -> int:
        domain: list = [("code", "=", code)]
        if company_id:
            domain.append(("company_id", "=", company_id))
        row = self._lookup(("journal", code, company_id), "account.journal", domain,
                           ["id", "code", "name"])
        if not row:
            raise OdooError(f"Journal with code '{code}' not found in Odoo")
        return row["id"]

    def analytic_account_id(self, code_or_name: str) -> int:
        row = self._lookup(("analytic", code_or_name), "account.analytic.account",
                           ["|", ("code", "=", code_or_name), ("name", "=", code_or_name)],
                           ["id", "name"])
        if not row:
            raise OdooError(f"Analytic account '{code_or_name}' not found in Odoo")
        return row["id"]

    def find_partner(self, name: Optional[str] = None, vat: Optional[str] = None,
                     email: Optional[str] = None) -> Optional[dict]:
        """Best-effort vendor/customer match: VAT, then exact name, then fuzzy name."""
        fields = ["id", "name", "vat", "email"]
        if vat:
            rows = self.search_read("res.partner", [("vat", "=", vat)], fields, limit=1)
            if rows:
                return rows[0]
        if email:
            rows = self.search_read("res.partner", [("email", "=ilike", email)], fields, limit=1)
            if rows:
                return rows[0]
        if name:
            rows = self.search_read("res.partner", [("name", "=ilike", name)], fields, limit=1)
            if rows:
                return rows[0]
            rows = self.search_read("res.partner", [("name", "ilike", name)], fields, limit=1)
            if rows:
                return rows[0]
        return None

    # ---- journal entries -------------------------------------------------------------
    def find_move_by_ref(self, ref: str, company_id: Optional[int] = None) -> Optional[dict]:
        domain: list = [("ref", "=", ref)]
        if company_id:
            domain.append(("company_id", "=", company_id))
        rows = self.search_read("account.move", domain, ["id", "name", "state", "ref"], limit=1)
        return rows[0] if rows else None

    def create_move(self, values: dict, post: bool = False) -> int:
        move_id = self.create("account.move", values)
        if post:
            self.call_method("account.move", "action_post", [move_id])
        return move_id

    def attach_file(self, model: str, res_id: int, filename: str, data_b64: str,
                    mimetype: str = "application/pdf") -> int:
        return self.create("ir.attachment", {
            "name": filename, "res_model": model, "res_id": res_id,
            "datas": data_b64, "mimetype": mimetype,
        })

    def chart_of_accounts(self, company_id: Optional[int] = None) -> list[dict]:
        ctx = {"allowed_company_ids": [company_id]} if company_id else None
        return self.search_read("account.account", [], ["code", "name", "account_type"],
                                context=ctx, order="code")
