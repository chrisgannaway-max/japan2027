"""Adding a portfolio from a file.

A wrong PMS or a mistyped report id does not announce itself until a pack arrives at six in the
morning and lands nowhere, so the checking is the point.
"""
import importlib
from datetime import time

import pytest

from conftest import FIXTURES
from portal import properties_import

CONFIG_DIR = FIXTURES.parent.parent / "config"
HEAD = ",".join(properties_import.COLUMNS)


def head(*rows):
    return "\n".join([HEAD] + list(rows)) + "\n"


def row(code="OKCAH", name="Hilton Garden Inn OKC Airport", brand="Hilton", pms="PEP",
        pid="OKCAH", pname="", company="", analytic="", journal="NA", due="", enabled="yes"):
    return ",".join([code, name, brand, pms, pid, pname, company, analytic, journal, due, enabled])


# ------------------------------------------------------------------ reading the file
def test_the_template_round_trips_through_its_own_reader():
    p = properties_import.parse(properties_import.template(), existing_codes=[])
    assert p.ok, p.errors
    assert [r.code for r in p.rows] == ["OKCON", "TXI47", "CANDLEWOOD-MOORE"]
    assert [r.pms for r in p.rows] == ["PEP", "CHOICEADV", "OPERA"]
    assert p.warnings == []                 # every example carries an id or a name


@pytest.mark.parametrize("given, expected", [
    ("Hilton", "PEP"), ("hilton", "PEP"), ("PEP", "PEP"),
    ("Choice", "CHOICEADV"), ("choice advantage", "CHOICEADV"), ("ChoiceAdvantage", "CHOICEADV"),
    ("Marriott", "AGILYSYS"), ("Wyndham", "SYNXIS"), ("IHG", "HOTELKEY"),
    ("opera-cloud", "OPERA"), ("  generic  ".strip(), "GENERIC"),
])
def test_the_pms_column_takes_the_brand_or_the_reader(given, expected):
    """Somebody filling this in from a portfolio page knows the brand, not our parser names."""
    p = properties_import.parse(head(row(pms=given)), [])
    assert p.ok, p.errors
    assert p.rows[0].pms == expected


def test_codes_are_upper_cased_and_the_header_is_forgiving():
    text = "﻿Code, Name ,PMS,PMS Property Id,Region\nokcah,HGI,Hilton,OKCAH,south\n"
    p = properties_import.parse(text, [])
    assert p.ok, p.errors
    assert p.rows[0].code == "OKCAH" and p.rows[0].fields["name"] == "HGI"


def test_only_code_and_pms_are_required():
    p = properties_import.parse("code,pms\nOKCAH,Hilton\n", [])
    # and a narrow file carries only what it named, so an update clears nothing else
    assert p.ok and set(p.rows[0].fields) == {"code", "pms"}
    assert p.warnings and "neither an id nor a name" in p.warnings[0]


def test_blank_lines_are_not_rows():
    p = properties_import.parse(head(row(), ",,,,,,,,,,", "   ,,,,,,,,,,"), [])
    assert p.ok and len(p.rows) == 1


# ------------------------------------------------------------------ refusing a bad file
@pytest.mark.parametrize("bad, expected", [
    (row(code="a"), "not a usable code"),
    (row(code="OKC AH"), "not a usable code"),
    (row(pms="Sabre"), "no reader for 'Sabre'"),
    (row(pms=""), "no reader for 'blank'"),
    (row(due="6am"), "Use a 24-hour time"),
    (row(due="26:00"), "Use a 24-hour time"),
    (row(enabled="sometimes"), "Use yes or no"),
])
def test_every_way_a_row_can_be_wrong_says_which_line_and_why(bad, expected):
    p = properties_import.parse(head(bad), [])
    assert not p.ok and len(p.errors) == 1
    assert expected in p.errors[0] and p.errors[0].startswith("Line 2")


def test_an_unknown_pms_lists_the_ones_that_work():
    p = properties_import.parse(head(row(pms="Sabre")), [])
    for known in ("PEP", "CHOICEADV", "AGILYSYS", "GENERIC"):
        assert known in p.errors[0]


def test_the_same_code_twice_names_the_earlier_line():
    p = properties_import.parse(head(row(code="OKCAH"), row(code="okcah")), [])
    assert not p.ok and "already on line 2" in p.errors[0]


def test_an_existing_code_is_an_update():
    p = properties_import.parse(head(row(code="OKCON")), existing_codes=["OKCON", "TXI47"])
    assert p.ok and p.rows[0].exists and p.rows[0].action == "update"


def test_an_empty_file_and_a_header_with_nothing_under_it():
    assert "empty" in properties_import.parse("", []).errors[0]
    assert "only a header" in properties_import.parse(head(), []).errors[0]
    assert "missing code, pms" in properties_import.parse("hotel,city\na,b\n", []).errors[0]


# ------------------------------------------------------------------ the page
def make(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_CONFIG", str(CONFIG_DIR / "properties.example.yaml"))
    monkeypatch.setenv("PORTAL_USERS", str(CONFIG_DIR / "users.example.yaml"))
    monkeypatch.setenv("PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("PORTAL_STORE", "db")
    for k in ("ODOO_URL", "ODOO_API_KEY", "ODOO_TRANSPORT", "REPORT_AT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import portal.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, follow_redirects=False)
    assert c.post("/login", data={"username": "admin", "password": "admin"}).status_code == 303
    return c, app_module


def upload(c, text):
    return c.post("/admin/import/hotels",
                  files=[("file", ("hotels.csv", text.encode(), "text/csv"))])


def test_the_template_is_offered_and_linked(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch)
    r = c.get("/admin/import/hotels.csv")
    assert r.status_code == 200 and "hotels.csv" in r.headers["content-disposition"]
    assert r.text.splitlines()[0] == HEAD
    assert "Import hotels" in c.get("/admin").text


def test_a_portfolio_lands_and_is_usable_straight_away(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch)
    r = upload(c, head(row(code="OKCAH", pms="Hilton", due="05:30"),
                       row(code="OKCBW", name="Best Western OKC", brand="Best Western",
                           pms="Generic", pid="OKCBW", journal="NA")))
    assert r.status_code == 200 and "2 hotels" in r.text and "2 added" in r.text

    props = app_module.state.props
    assert props["OKCAH"]["pms"] == "PEP" and props["OKCAH"]["due_by"] == "05:30"
    assert props["OKCBW"]["pms"] == "GENERIC"
    # the cut-off is not just stored, it is the one the morning list uses
    from portal import daily
    assert daily.due_by(props["OKCAH"]) == time(5, 30)
    assert daily.due_by(props["OKCBW"]) == time(6, 0)        # blank means the default

    # a manager can be given the new hotel, which means it is a real property everywhere
    assert "OKCAH" in c.get("/admin").text


def test_one_bad_row_writes_nothing_at_all(tmp_path, monkeypatch):
    c, app_module = make(tmp_path, monkeypatch)
    before = set(app_module.state.props)
    r = upload(c, head(row(code="GOOD1"), row(code="BAD1", pms="Sabre"), row(code="GOOD2")))
    assert r.status_code == 200 and "Nothing was imported" in r.text and "Line 3" in r.text
    assert set(app_module.state.props) == before


def test_an_update_keeps_the_mapping_and_anything_the_file_leaves_out(tmp_path, monkeypatch):
    """The trap this guards: a file of codes and names must not wipe an afternoon's mapping."""
    c, app_module = make(tmp_path, monkeypatch)
    store = app_module.state.store
    mapping = (CONFIG_DIR / "gl_mapping" / "hilton_pep.example.yaml").read_text()
    store.save_property(code="OKCON", name="Embassy Suites", pms="PEP", pms_property_id="OKCON",
                        company="Champion OKC LLC", analytic="OKCON", journal="NA",
                        mapping_yaml=mapping, enabled="1")

    r = upload(c, "code,pms,name\nOKCON,PEP,Embassy Suites by Hilton OKC Northwest\n")
    assert r.status_code == 200 and "1 updated" in r.text

    after = store.get_property("OKCON")
    assert after["name"] == "Embassy Suites by Hilton OKC Northwest"     # changed
    assert after["mapping_yaml"] == mapping                             # untouched
    assert after["company"] == "Champion OKC LLC" and after["analytic"] == "OKCON"
    assert after["journal"] == "NA" and after["pms_property_id"] == "OKCON"


def test_a_hotel_with_no_report_id_is_flagged_not_refused(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch)
    r = upload(c, "code,pms\nOKCXX,Hilton\n")
    assert r.status_code == 200 and "1 hotel" in r.text
    assert "Worth a look" in r.text and "cannot be matched to it" in r.text


def test_importing_needs_the_database_store(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, PORTAL_STORE="yaml")
    assert upload(c, head(row())).status_code == 400


def test_a_manager_cannot_import_hotels(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch)
    c.get("/logout")
    c.post("/login", data={"username": "okcon", "password": "okcon"})
    assert upload(c, head(row())).status_code in (303, 403)
    assert c.get("/admin/import/hotels.csv").status_code in (303, 403)
