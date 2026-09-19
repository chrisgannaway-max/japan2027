"""One clock for the whole portal, set to the hotels' time.

Two different times are needed and they are not the same time:

    clock.now()     the wall clock where the hotels are.  "Due by 06:00" means six in the
                    morning in Oklahoma, and the business date rolls over at midnight there --
                    not at midnight wherever the server happens to be running.  Render runs
                    in UTC, so without this the morning list went out at 1am and a pack that
                    arrived at 7pm was filed against the wrong night.

    clock.stamp()   what goes in a database column: UTC, to the second.  Stamps are compared
                    and sorted as plain strings all over the store, and they decide which of
                    two uploads supersedes the other.  Local time cannot do that job: on the
                    first Sunday in November it runs the same hour twice, and an upload made
                    at 01:30 would sort before one made an hour earlier.

    clock.show()    a stored stamp, printed back in the hotels' time, because that is the
                    only time anybody reading the screen is thinking in.

Set TIMEZONE to move it -- any name from the IANA database, e.g. America/New_York.  An
unknown name falls back to UTC with a line on the console rather than refusing to start,
because a typo in a setting should not take the site down at 3am.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Oklahoma: Central time, and it does observe daylight saving.
DEFAULT_TZ = "America/Chicago"


def _zone() -> ZoneInfo:
    name = os.environ.get("TIMEZONE", DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        print(f"[portal] TIMEZONE '{name}' is not a timezone I know; using UTC")
        return ZoneInfo("UTC")


#: Read once.  Changing the hotels' timezone is a restart, not a request.
TZ = _zone()


def now() -> datetime:
    """The wall clock where the hotels are, without a tzinfo attached.

    Naive on purpose: every comparison in the portal is against a naive `datetime.combine`
    of a business date and a cut-off time, and mixing the two kinds raises.
    """
    return datetime.now(timezone.utc).astimezone(TZ).replace(tzinfo=None)


def today() -> date:
    return now().date()


def utc_now() -> datetime:
    """UTC, for the few places that need to do arithmetic on a stored stamp."""
    return datetime.now(timezone.utc)


def stamp() -> str:
    """The string written to a timestamp column: UTC, to the second."""
    return utc_now().strftime("%Y-%m-%dT%H:%M:%S")


def to_local(value: Union[str, datetime, None]) -> Optional[datetime]:
    """A stored stamp as a datetime in the hotels' time, or None if it is not one.

    A stamp with no offset is read as UTC, which is what the site has always written: it runs
    in UTC, and `stamp()` now says so explicitly.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


def show(value: Union[str, datetime, None], fmt: str = "%m/%d/%Y %I:%M %p %Z") -> str:
    """A stored stamp, printed in the hotels' time: "09/18/2026 5:38 pm CDT".

    Anything that is not a stamp is handed back untouched, so a half-filled row prints what
    it has rather than an error page.  The hour is trimmed by hand because the format that
    does it for you is not the same one on every platform.
    """
    dt = to_local(value)
    if dt is None:
        return "" if not value else str(value)
    out = dt.strftime(fmt).replace("AM", "am").replace("PM", "pm")
    return re.sub(r"\b0(\d:)", r"\1", out)


def show_short(value: Union[str, datetime, None]) -> str:
    """The same, without the year: "09/18 5:38 pm CDT"."""
    return show(value, "%m/%d %I:%M %p %Z")
