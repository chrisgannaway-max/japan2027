"""The thing that makes it run without anybody.

Two jobs on a loop, both small:

    drain the posting queue   a night that failed at 3am should not wait until tomorrow's
                              e-mail arrives before it is tried again
    send the morning list     once a day, after the last property's cut-off

Deliberately a thread in the web process rather than a separate worker.  One property group's
night audit is a handful of files and a couple of API calls; a queue broker would be more
moving parts than the work it carries.  It is off unless SCHEDULER=on, because something that
writes to a client's accounting system on a timer should be switched on deliberately.

`tick()` holds all the decisions and takes the time as an argument, so what happens at 05:59
and 06:01 is a test rather than a wait.
"""
from __future__ import annotations

import os
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from . import daily, mail, poster

#: how often the loop wakes.  Small enough that a failed night retries within the hour, large
#: enough that an idle night is nearly free.
INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_INTERVAL", "600"))
LAST_REPORT_KEY = "DAILY_REPORT_SENT"     # the business date last reported on


@dataclass
class TickResult:
    posted: int = 0
    failed: int = 0
    report_sent: bool = False
    report_why: str = ""

    def __str__(self) -> str:
        bits = []
        if self.posted or self.failed:
            bits.append(f"{self.posted} posted, {self.failed} failed")
        if self.report_sent:
            bits.append("daily report sent")
        elif self.report_why:
            bits.append(f"no report ({self.report_why})")
        return "; ".join(bits) or "nothing to do"


def business_date_for(now: datetime) -> date:
    """A pack covers the night before, so the morning of the 12th reports on the 11th."""
    return now.date() - timedelta(days=1)


def report_is_due(state, now: datetime) -> tuple[bool, str]:
    """After every property's cut-off, and not already sent for that night."""
    day = business_date_for(now)
    if str(state.db.settings().get(LAST_REPORT_KEY, "")) == day.isoformat():
        return False, "already sent"
    if not state.props:
        return False, "no properties"
    latest = max(daily.due_by(p) for p in state.props.values())
    if now < datetime.combine(now.date(), latest):
        return False, f"before the {latest.strftime('%H:%M')} cut-off"
    return True, ""


def send_report(state, now: Optional[datetime] = None, force: bool = False) -> tuple[bool, str]:
    now = now or datetime.now()
    day = business_date_for(now)
    to = mail.setting("REPORT_TO")
    if not to:
        return False, "no REPORT_TO address configured"
    report = daily.build(state.db, state.props, day, now)
    subject = (f"Night audit {day.isoformat()}: all clear" if report.all_clear
               else f"Night audit {day.isoformat()}: {len(report.trouble)} need attention")
    sent, why = mail.send_reporting(to, subject, daily.as_text(report, mail.base_url()))
    if sent:
        state.db.save_settings({LAST_REPORT_KEY: day.isoformat()}, "scheduler")
    return sent, why


def tick(state, now: Optional[datetime] = None) -> TickResult:
    """One pass.  Never raises: a background loop that dies takes the whole thing with it."""
    now = now or datetime.now()
    out = TickResult()
    try:
        if state.delivery == "odoo" and state.odoo_enabled:
            s = poster.post_due(state.db, autopost=state.autopost)
            out.posted, out.failed = len(s.posted), len(s.failed)
    except Exception as e:                       # noqa: BLE001
        out.report_why = f"posting failed: {e}"
    try:
        due, why = report_is_due(state, now)
        if due:
            out.report_sent, out.report_why = send_report(state, now)
        else:
            out.report_why = why
    except Exception as e:                       # noqa: BLE001
        out.report_why = f"report failed: {e}"
    return out


def start(state) -> Optional[threading.Thread]:
    if os.environ.get("SCHEDULER", "off").lower() not in ("1", "on", "yes", "true"):
        return None

    def loop() -> None:
        while True:
            result = tick(state)
            if result.posted or result.failed or result.report_sent:
                print(f"[scheduler] {result}")
            _time.sleep(INTERVAL_SECONDS)

    t = threading.Thread(target=loop, name="nightaudit-scheduler", daemon=True)
    t.start()
    print(f"[scheduler] on, every {INTERVAL_SECONDS}s")
    return t
