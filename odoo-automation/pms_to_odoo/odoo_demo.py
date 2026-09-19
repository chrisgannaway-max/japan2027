"""A pretend Odoo, so the whole path can be rehearsed before there is a real one.

Set `ODOO_TRANSPORT=demo` with `DELIVERY_MODE=odoo` and the portal behaves exactly as it will
against the client's server: *Send to Odoo* works, an entry comes back with a number, the run
turns *posted*, and sending the same night twice finds the first one instead of writing a
second.  Nothing leaves the process.

It exists for two jobs the real client cannot do yet:

  * proving the plumbing -- that a night parsed, mapped and balanced actually survives the
    trip to Odoo's API and back -- while the client is still deciding which Odoo plan they
    are on and who the bot user will be;
  * showing somebody what the finished thing does, without asking for credentials first.

Deliberately more forgiving than the fake used in the tests, which asserts on anything it was
not expecting because that strictness is what catches our bugs.  This one **invents master
data on demand**: ask for GL account 4010 or a journal called NA and it makes one up and
remembers it.  A rehearsal should fail on the things that will really fail -- an entry that
does not balance, a night already sent -- and not on a chart of accounts nobody has loaded.

It forgets everything when the process restarts.  Entry numbers therefore start again, while
the portal's own record of what it sent does not: a run can say "posted as NA/DEMO/0001" long
after this has forgotten writing it.  That is the honest cost of a stand-in and the reason
every screen that shows it says DEMO.
"""
from __future__ import annotations

import itertools
from typing import Any, Optional

#: What a move is called.  Unmistakable in a screenshot, and it sorts next to nothing real.
MOVE_PREFIX = "NA/DEMO/"


class DemoTransport:
    """Answers the calls OdooClient makes, in memory."""

    #: models whose rows are conjured up rather than looked up
    INVENTED = {
        "res.company": lambda d: {"name": d.get("name") or "Demo Company"},
        "account.account": lambda d: {"code": d.get("code") or "0000",
                                      "name": f"Account {d.get('code') or '0000'}",
                                      "account_type": "demo"},
        "account.journal": lambda d: {"code": d.get("code") or "NA",
                                      "name": f"Journal {d.get('code') or 'NA'}"},
        "account.analytic.account": lambda d: {"code": d.get("code") or d.get("name") or "",
                                               "name": d.get("name") or d.get("code") or "Demo"},
    }

    def __init__(self):
        self.ids = itertools.count(1)
        self.seq = itertools.count(1)
        self.records: dict[str, list[dict]] = {}
        self.calls: list[tuple] = []

    # -- the one method a transport has to have -------------------------------------
    def call(self, model: str, method: str, ids=None, context=None, **kw) -> Any:
        self.calls.append((model, method, ids, kw))
        if method == "fields_get":
            # The demo stands in for a current Odoo, so it has the modern analytic field.
            fields = {"account_id", "name", "debit", "credit", "partner_id", "move_id",
                      "analytic_distribution", "ref", "state"}
            return {f: {"type": "char"} for f in fields}
        if method == "search_read":
            return self._search_read(model, kw)
        if method == "create":
            return [self._create(model, vals) for vals in kw["vals_list"]]
        if method == "write":
            for r in self._rows(model):
                if r["id"] in (ids or []):
                    r.update(kw.get("vals") or {})
            return True
        if method == "action_post":
            for r in self._rows(model):
                if r["id"] in (ids or []):
                    r["state"] = "posted"
            return True
        # Anything else is something the real client does that this does not model yet.  Say
        # so plainly: a rehearsal that quietly returns nothing teaches the wrong lesson.
        raise NotImplementedError(f"the demo Odoo does not implement {model}.{method}")

    # -- internals -------------------------------------------------------------------
    def _rows(self, model: str) -> list[dict]:
        return self.records.setdefault(model, [])

    def _create(self, model: str, vals: dict) -> int:
        rec = {"id": next(self.ids), **vals}
        if model == "account.move":
            rec.setdefault("name", f"{MOVE_PREFIX}{next(self.seq):04d}")
            rec.setdefault("state", "draft")
        rec.setdefault("name", f"{model}/{rec['id']}")
        self._rows(model).append(rec)
        return rec["id"]

    def _search_read(self, model: str, kw: dict) -> list[dict]:
        domain = kw.get("domain") or []
        rows = [r for r in self._rows(model) if _matches(r, domain)]
        if not rows and model in self.INVENTED:
            wanted = _wanted_values(domain)
            # A `code in [...]` prefetch asks for many at once; make each one.
            codes = wanted.pop("code__in", None)
            made = []
            for code in (codes if codes is not None else [None]):
                d = dict(wanted)
                if code is not None:
                    d["code"] = code
                if codes is None and not d:
                    continue
                rec_id = self._create(model, self.INVENTED[model](d))
                made.append(self._rows(model)[-1])
            rows = [r for r in made if _matches(r, domain)] or made
        return rows[: kw["limit"]] if kw.get("limit") else rows


def _matches(rec: dict, domain: list) -> bool:
    terms = [t for t in domain if t != "|"]
    use_or = "|" in domain
    out = []
    for f, op, v in terms:
        val = rec.get(f)
        if isinstance(val, dict):
            val = val.get("id")
        if op == "=":
            out.append(val == v)
        elif op in ("ilike", "=ilike"):
            out.append(isinstance(val, str) and str(v).lower().strip("%") in val.lower())
        elif op == "in":
            out.append(val in (v or []))
        else:
            out.append(False)
    if not out:
        return True
    return any(out) if use_or else all(out)


def _wanted_values(domain: list) -> dict:
    """What the caller was looking for, so a row can be invented that answers it."""
    out: dict[str, Any] = {}
    for t in domain:
        if t == "|" or len(t) != 3:
            continue
        f, op, v = t
        if op == "in" and f == "code":
            out["code__in"] = list(v or [])
        elif op in ("=", "ilike", "=ilike") and isinstance(v, str):
            out.setdefault(f, v.strip("%"))
    return out


def transport(_settings: Optional[object] = None) -> DemoTransport:
    """One per process, so two nights in a row see the same books."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = DemoTransport()
        print("[odoo] DEMO transport: entries are kept in memory and reach no Odoo server")
    return _SINGLETON


_SINGLETON: Optional[DemoTransport] = None
