"""One clock, set to the hotels' time.

The site runs on a host whose clock is UTC and the people reading it are in Oklahoma, so the
two have to be kept apart on purpose: decisions on the hotels' wall clock, stamps in UTC.
"""
import importlib
from datetime import date, datetime, timedelta, timezone

import pytest

from portal import clock


@pytest.fixture()
def central(monkeypatch):
    monkeypatch.delenv("TIMEZONE", raising=False)
    return importlib.reload(clock)


def test_stamps_are_utc_and_sort(central):
    s = central.stamp()
    parsed = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    assert abs(parsed - datetime.now(timezone.utc).replace(tzinfo=None)) < timedelta(seconds=5)
    # The format has to stay sortable as plain text: the store compares these as strings to
    # decide which of two uploads supersedes the other.
    assert sorted(["2026-11-01T06:30:00", "2026-11-01T07:30:00"]) == \
        ["2026-11-01T06:30:00", "2026-11-01T07:30:00"]


def test_now_is_the_hotels_wall_clock(central):
    # Naive, so it can be compared with datetime.combine(business_date, due_by) as it is
    # everywhere in the report.
    assert central.now().tzinfo is None
    offset = central.now() - datetime.now(timezone.utc).replace(tzinfo=None)
    assert timedelta(hours=-6, minutes=-1) < offset < timedelta(hours=-4, minutes=-59)
    assert central.today() == central.now().date()


def test_show_prints_a_stored_stamp_in_central_time(central):
    assert central.show("2026-09-18T22:38:29") == "09/18/2026 5:38 pm CDT"
    assert central.show_short("2026-09-18T22:38:29") == "09/18 5:38 pm CDT"
    assert central.show("2026-01-15T14:00:00") == "01/15/2026 8:00 am CST"   # winter
    # Late evening in Oklahoma is already tomorrow in UTC, which is what made the raw column
    # read a day ahead.
    assert central.show("2026-09-19T00:30:00").startswith("09/18/2026 7:30 pm")


def test_a_time_already_on_the_hotels_clock_is_not_shifted_again(central):
    from datetime import datetime as dt
    # scheduler.status() hands the page the next send time as a local wall clock, not a stamp.
    assert central.show_local(dt(2026, 9, 19, 6, 0)) == "09/19 6:00 am"
    assert central.show_local(None) == ""


def test_show_leaves_alone_what_is_not_a_stamp(central):
    assert central.show(None) == "" and central.show("") == ""
    assert central.show("not a date") == "not a date"      # a page prints what it has


def test_a_stamp_that_carries_its_own_offset_is_honoured(central):
    # An e-mail's Date header arrives in whatever zone the sending server keeps.
    assert central.show("2026-09-18T22:38:29+00:00") == "09/18/2026 5:38 pm CDT"
    assert central.show("2026-09-18T17:38:29-05:00") == "09/18/2026 5:38 pm CDT"


def test_timezone_is_a_setting(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/New_York")
    c = importlib.reload(clock)
    assert c.show("2026-09-18T22:38:29") == "09/18/2026 6:38 pm EDT"
    monkeypatch.setenv("TIMEZONE", "Mars/Olympus")     # a typo must not stop the site starting
    c = importlib.reload(clock)
    assert c.show("2026-09-18T22:38:29") == "09/18/2026 10:38 pm UTC"
    monkeypatch.delenv("TIMEZONE")
    importlib.reload(clock)
