"""In-memory stand-in for the Odoo transport so tests never touch a server."""
from __future__ import annotations

import itertools


class FakeTransport:
    #: which Odoo this is pretending to be.  17+ tags a line with analytic_distribution,
    #: 16 and earlier with analytic_account_id, and a server without the analytic module
    #: has neither -- all three are real deployments somebody might point us at.
    ANALYTIC = {"modern": "analytic_distribution", "legacy": "analytic_account_id", "none": ""}

    def __init__(self, analytic: str = "modern"):
        self.analytic = self.ANALYTIC[analytic]
        self.ids = itertools.count(100)
        self.calls: list[tuple] = []
        self.records: dict[str, list[dict]] = {
            "res.company": [{"id": 1, "name": "Example Hotel LLC"}],
            "account.journal": [{"id": 7, "code": "NA", "name": "Night Audit", "company_id": 1},
                                {"id": 8, "code": "BILL", "name": "Vendor Bills", "company_id": 1}],
            "account.account": [{"id": 10 + i, "code": c, "name": c, "account_type": "x"}
                                for i, c in enumerate(["4000", "4010", "4020", "4100", "4200", "4900", "4950",
                                                       "2200", "1010", "1120", "1121", "1122", "1200", "1130",
                                                       "1210", "2300", "6110", "6900"])],
            "account.analytic.account": [{"id": 50, "code": "HGI-EXAMPLE", "name": "HGI Example"}],
            "res.partner": [{"id": 60, "name": "Direct Bill", "vat": False, "email": False},
                            {"id": 61, "name": "Acme Linen Supply", "vat": "12-3456789", "email": "ar@acmelinen.com"}],
            "account.move": [],
            "ir.attachment": [],
        }

    def call(self, model, method, ids=None, context=None, **kw):
        self.calls.append((model, method, ids, kw))
        if method == "fields_get":
            base = {"account_id", "name", "debit", "credit", "partner_id", "move_id"}
            if model == "account.move.line" and self.analytic:
                base.add(self.analytic)
            return {f: {"type": "char"} for f in base}
        if method == "search_read":
            rows = [r for r in self.records.get(model, []) if self._match(r, kw.get("domain", []))]
            return rows[: kw["limit"]] if kw.get("limit") else rows
        if method == "create":
            out = []
            for vals in kw["vals_list"]:
                rec = {"id": next(self.ids), **vals}
                rec.setdefault("name", f"{model}/{rec['id']}")
                rec.setdefault("state", "draft")
                self.records.setdefault(model, []).append(rec)
                out.append(rec["id"])
            return out
        if method == "action_post":
            for r in self.records["account.move"]:
                if r["id"] in ids:
                    r["state"] = "posted"
            return True
        raise AssertionError(f"unexpected call {model}.{method}")

    @staticmethod
    def _match(rec, domain):
        # supports simple AND domains plus the leading "|" used by lookups
        terms = [t for t in domain if t != "|"]
        use_or = "|" in domain
        results = []
        for f, op, v in terms:
            val = rec.get(f)
            if isinstance(val, dict):
                val = val.get("id")
            if op == "=":
                results.append(val == v)
            elif op in ("ilike", "=ilike"):
                results.append(isinstance(val, str) and str(v).lower().strip("%") in val.lower())
            elif op == "in":
                results.append(val in (v or []))
            else:
                raise AssertionError(f"fake Odoo does not implement the {op!r} operator")
        return any(results) if use_or else all(results)
