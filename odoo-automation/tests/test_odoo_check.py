"""Asking Odoo everything a night will need, before a night depends on the answer.

Each of these failures otherwise arrives one at a time, on consecutive mornings, each costing a
round trip to somebody at the client.
"""
import importlib
from pathlib import Path

import pytest

from conftest import FIXTURES
from fake_odoo import FakeTransport
from pms_to_odoo import odoo_check
from pms_to_odoo.journal import entry_to_odoo_values
from pms_to_odoo.mapping import GLMapping
from pms_to_odoo.odoo_client import OdooClient
from pms_to_odoo.parsers import get_parser
from pms_to_odoo.pipeline import load_properties

CONFIG = FIXTURES.parent.parent / "config"


@pytest.fixture()
def props():
    return load_properties(CONFIG / "properties.example.yaml")


def client(**kw):
    return OdooClient(FakeTransport(**kw))


def stocked(props, **kw):
    """A fake Odoo set up the way the mappings expect: every analytic account and GL code.

    "Healthy" has to mean something, and what it means here is a server somebody has already
    configured to match the chart of accounts we are posting against.
    """
    t = FakeTransport(**kw)
    next_id = 900
    for code, prop in props.items():
        mapping = odoo_check._mapping(prop)
        if mapping is None:
            continue
        if mapping.analytic:
            t.records["account.analytic.account"].append(
                {"id": next_id, "code": mapping.analytic, "name": mapping.analytic})
            next_id += 1
        have = {r["code"] for r in t.records["account.account"]}
        for acct in odoo_check._mapped_codes(mapping):
            if acct not in have:
                t.records["account.account"].append(
                    {"id": next_id, "code": acct, "name": acct, "account_type": "x"})
                have.add(acct)
                next_id += 1
    return OdooClient(t)


# ------------------------------------------------- which field this Odoo wants
@pytest.mark.parametrize("flavour, expected", [
    ("modern", "analytic_distribution"),      # Odoo 17 and later
    ("legacy", "analytic_account_id"),        # Odoo 16 and earlier
    ("none", ""),                             # analytics not installed
])
def test_the_analytic_field_is_asked_for_not_assumed(flavour, expected):
    assert client(analytic=flavour).analytic_field() == expected


@pytest.mark.parametrize("flavour", ["modern", "legacy", "none"])
def test_a_line_is_tagged_the_way_this_server_expects(flavour):
    """Guessing wrong here is an entry Odoo refuses, on every property, every night."""
    props = load_properties(CONFIG / "properties.example.yaml")
    report = get_parser("PEP").parse(FIXTURES / "pep_final_audit.txt", "OKCON")
    entry = GLMapping.load(CONFIG / "gl_mapping" / "hilton_pep.example.yaml").build_entry(report)
    c = stocked(props, analytic=flavour)
    lines = [l[2] for l in entry_to_odoo_values(entry, c)["line_ids"]]

    if flavour == "none":
        assert not any("analytic_distribution" in v or "analytic_account_id" in v for v in lines)
        return                                  # untagged beats refused

    okcon = c.analytic_account_id("OKCON")
    tagged = [v for v in lines if "analytic_distribution" in v or "analytic_account_id" in v]
    assert tagged, "the mapping tags every line with the property"
    if flavour == "modern":                     # Odoo 17 and later: a distribution map
        assert all(v["analytic_distribution"] == {str(okcon): 100} for v in tagged)
        assert not any("analytic_account_id" in v for v in tagged)
    else:                                       # Odoo 16 and earlier: a single id
        assert all(v["analytic_account_id"] == okcon for v in tagged)
        assert not any("analytic_distribution" in v for v in tagged)


def test_the_field_is_asked_for_once_not_per_line():
    c = client()
    for _ in range(5):
        c.analytic_field()
    assert sum(1 for m, meth, _, _ in c.t.calls if meth == "fields_get") == 1


# ------------------------------------------------- the whole preflight
def test_a_healthy_server_reports_every_property_ready(props):
    check = odoo_check.run(stocked(props), props)
    assert check.reachable and check.ok
    assert check.analytic_field == "analytic_distribution"
    assert len(check.properties) == len(props)
    assert all(p.journal == "NA" and p.company and p.analytic for p in check.properties)
    assert all(not p.missing_accounts for p in check.properties)
    assert "All 6 properties look ready" in check.summary


def test_a_missing_gl_account_is_named_rather_than_counted(props):
    c = stocked(props)
    gone = {"4000", "1010"}
    c.t.records["account.account"] = [r for r in c.t.records["account.account"]
                                      if r["code"] not in gone]
    check = odoo_check.run(c, props)
    assert not check.ok
    missing = {a for p in check.properties for a in p.missing_accounts}
    assert gone <= missing
    assert "would not post yet" in check.summary


def test_a_missing_journal_is_reported_per_property(props):
    c = stocked(props)
    c.t.records["account.journal"] = []
    check = odoo_check.run(c, props)
    assert not check.ok
    assert all(any("Journal" in x for x in p.problems) for p in check.properties)


def test_an_unreachable_server_says_so_and_checks_nothing(props):
    class Dead:
        def call(self, *a, **kw):
            from pms_to_odoo.odoo_client import OdooError
            raise OdooError("Could not reach Odoo at https://odoo.example.com: timed out")

    check = odoo_check.run(OdooClient(Dead()), props)
    assert not check.reachable and not check.ok and check.properties == []
    assert "timed out" in check.summary and "Could not use this Odoo" in check.summary


def test_a_check_creates_nothing(props):
    """It has to be safe to run against the client's live server."""
    c = stocked(props)
    odoo_check.run(c, props)
    assert not [m for m, meth, _, _ in c.t.calls if meth in ("create", "write", "action_post")]


def test_the_property_page_disagreeing_with_the_mapping_is_flagged(props):
    p = dict(props["OKCON"])
    p["journal"] = "SALES"                      # the mapping posts to NA
    check = odoo_check.run(stocked(props), {"OKCON": p})
    assert not check.ok
    assert any("page says the journal is 'SALES'" in x for x in check.properties[0].problems)


def test_a_property_with_no_mapping_is_not_silently_ready(props):
    p = {k: v for k, v in props["OKCON"].items() if k != "gl_mapping"}
    check = odoo_check.run(stocked(props), {"OKCON": p})
    assert not check.ok and "no GL mapping" in check.properties[0].problems[0]


# ------------------------------------------------- the button
def test_the_page_runs_it_and_the_demo_says_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CONFIG / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CONFIG / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("DELIVERY_MODE", "odoo")
    monkeypatch.setenv("ODOO_TRANSPORT", "demo")
    for k in ("ODOO_URL", "ODOO_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    import pms_to_odoo.odoo_demo as demo
    importlib.reload(demo)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, follow_redirects=False)
    c.post("/login", data={"username": "admin", "password": "admin"})

    assert "Check the Odoo connection" in c.get("/admin").text
    r = c.post("/admin/odoo/check")
    assert r.status_code == 200
    assert "analytic_distribution" in r.text and "DEMO" in r.text
    assert "look ready to post" in r.text


# ----------------------------------------------- the XML-RPC calling convention
class SpyProxy:
    """Stands in for xmlrpc.client.ServerProxy and records how execute_kw was called."""

    def __init__(self):
        self.calls = []

    def execute_kw(self, db, uid, key, model, method, args, kw):
        self.calls.append({"model": model, "method": method, "args": args, "kwargs": kw})
        return [1]


def xmlrpc_transport():
    """A transport without a server: __init__ authenticates, and there is nothing to talk to."""
    from pms_to_odoo.odoo_client import XmlRpcTransport
    t = object.__new__(XmlRpcTransport)
    t.db, t.uid, t.secret, t.url = "champion_test", 2, "key", "http://localhost:8069"
    t._models = SpyProxy()
    return t


def test_create_sends_its_values_positionally():
    """Odoo reads create's values out of args[0] before the method runs.

    Sent by name they never arrive, args is empty, and Odoo answers "IndexError: tuple index
    out of range" -- which says nothing about what is wrong. This broke every write over
    XML-RPC, journal entries included, and was invisible until something met a real server.
    """
    t = xmlrpc_transport()
    t.call("account.account", "create", vals_list=[{"code": "4000"}], context={"x": 1})
    call = t._models.calls[0]
    assert call["args"] == [[{"code": "4000"}]], "values must be the first positional argument"
    assert "vals_list" not in call["kwargs"]
    assert call["kwargs"] == {"context": {"x": 1}}, "context still travels by name"


def test_other_methods_still_pass_their_parameters_by_name():
    """Only create has the rule; sending search_read's domain positionally would break it."""
    t = xmlrpc_transport()
    t.call("account.account", "search_read", domain=[("code", "=", "4000")], fields=["id"])
    call = t._models.calls[0]
    assert call["args"] == []
    assert call["kwargs"] == {"domain": [("code", "=", "4000")], "fields": ["id"]}


def test_ids_stay_first_when_a_method_acts_on_records():
    t = xmlrpc_transport()
    t.call("account.move", "action_post", ids=[7])
    assert t._models.calls[0]["args"] == [[7]]
