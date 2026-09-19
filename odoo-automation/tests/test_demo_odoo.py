"""The stand-in Odoo, for rehearsing the whole path before the client has a server.

What matters is that it behaves like the real one where it counts -- a night comes back with a
number, the same night sent twice finds the first -- and that it is never mistaken for real.
"""
import importlib
from decimal import Decimal

import pytest

from conftest import FIXTURES
from pms_to_odoo import odoo_demo
from pms_to_odoo.journal import post_entry
from pms_to_odoo.mapping import GLMapping
from pms_to_odoo.odoo_client import OdooClient, OdooSettings
from pms_to_odoo.parsers import get_parser

CONFIG = FIXTURES.parent.parent / "config" / "gl_mapping"


@pytest.fixture()
def demo(monkeypatch):
    monkeypatch.setenv("ODOO_TRANSPORT", "demo")
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("ODOO_API_KEY", raising=False)
    importlib.reload(odoo_demo)                    # a fresh set of books per test
    return OdooClient.connect(OdooSettings.from_env())


@pytest.fixture(scope="module")
def entry():
    report = get_parser("PEP").parse(FIXTURES / "pep_final_audit.txt", "OKCON")
    return GLMapping.load(CONFIG / "hilton_pep.example.yaml").build_entry(report)


def test_it_connects_without_a_url_or_a_key(monkeypatch):
    monkeypatch.setenv("ODOO_TRANSPORT", "demo")
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("ODOO_API_KEY", raising=False)
    s = OdooSettings.from_env()
    assert s.transport == "demo"
    assert OdooClient.connect(s) is not None


def test_master_data_is_invented_rather_than_missing(demo):
    """A rehearsal must not fail on a chart of accounts nobody has loaded yet."""
    assert demo.journal_id("NA") and demo.account_id("4000") and demo.company_id("Champion Hotels")
    assert demo.analytic_account_id("OKCON")
    assert demo.account_id("4000") == demo.account_id("4000")     # and stays the same
    assert demo.account_id("4010") != demo.account_id("4000")


def test_a_night_goes_over_and_the_same_night_does_not_go_twice(demo, entry):
    first = post_entry(entry, demo, post=False)
    assert first.status == "created" and first.move_id
    again = post_entry(entry, demo, post=False)
    assert again.status == "exists" and again.move_id == first.move_id
    assert odoo_demo.MOVE_PREFIX in again.message        # NA/DEMO/0001: unmistakable

    # A second connection is the same books, as it would be with a real server.
    other = OdooClient.connect(OdooSettings.from_env())
    assert post_entry(entry, other, post=False).status == "exists"


def test_it_still_refuses_what_the_real_one_would(demo, entry):
    broken = GLMapping.load(CONFIG / "hilton_pep.example.yaml").build_entry(
        get_parser("PEP").parse(FIXTURES / "pep_final_audit.txt", "OKCON"))
    broken.lines[0].debit += Decimal("1.00")
    with pytest.raises(Exception) as e:
        post_entry(broken, demo)
    assert "unbalanced" in str(e.value).lower()


def test_an_unmodelled_call_says_so_instead_of_pretending(demo):
    with pytest.raises(NotImplementedError):
        demo.t.call("account.move", "unlink", ids=[1])
