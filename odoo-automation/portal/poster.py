"""Sending balanced nights to Odoo, one property at a time.

Nothing here runs inside the request that received the e-mail.  A hotel's pack arriving must
not wait on Odoo being awake, and Odoo being slow must not make a mail provider think delivery
failed and retry.  So arrival and posting are separate: intake records the night, this drains
what is waiting.

One property per call, deliberately.  A batch that posts seven hotels together fails as a
batch, and then nobody can say which hotels are in the books and which are not.  Sent one at a
time, a failure is one row still waiting, and a retry touches only that row.

Re-running is safe at two levels.  A run that posted has `posted_at` set and is no longer
selected; and even if it were, post_entry looks for the reference in Odoo first and returns the
move that is already there rather than making a second one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from pms_to_odoo.journal import post_entry
from pms_to_odoo.odoo_client import OdooClient, OdooError, OdooSettings
from pms_to_odoo.pipeline import RunResult

MAX_ATTEMPTS = 5


@dataclass
class PostSummary:
    posted: list[tuple[int, str]] = field(default_factory=list)      # (run_id, ref)
    existing: list[tuple[int, str]] = field(default_factory=list)    # already in Odoo
    failed: list[tuple[int, str]] = field(default_factory=list)      # (run_id, error)
    skipped: int = 0

    def __str__(self) -> str:
        return (f"{len(self.posted)} posted, {len(self.existing)} already there, "
                f"{len(self.failed)} failed, {self.skipped} skipped")

    @property
    def did_anything(self) -> bool:
        return bool(self.posted or self.existing or self.failed)


def post_due(db, *, autopost: bool = False, limit: int = 50, max_attempts: int = MAX_ATTEMPTS,
             connect: Optional[Callable[[], OdooClient]] = None) -> PostSummary:
    """Send every balanced night that has not reached Odoo yet.

    ``autopost`` decides whether the move is posted or left in draft; draft is the default
    everywhere, because a draft can be deleted and a posted entry needs a reversing entry.
    """
    summary = PostSummary()
    waiting = db.runs_awaiting_post(limit=limit, max_attempts=max_attempts)
    if not waiting:
        return summary

    try:
        client = (connect or (lambda: OdooClient.connect(OdooSettings.from_env())))()
    except OdooError as e:
        # Not configured or not reachable: leave everything where it is.  This is not the
        # failure of any one night, so it must not count against any night's attempts.
        for r in waiting:
            summary.failed.append((r["id"], f"Odoo unavailable: {e}"))
        return summary

    for r in waiting:
        res = RunResult.from_json(r["result_json"])
        if res.entry is None or res.entry.imbalance != 0:
            summary.skipped += 1                      # should not be selected; belt and braces
            continue
        try:
            out = post_entry(res.entry, client, post=autopost)
        except OdooError as e:
            db.record_post_failure(r["id"], str(e))
            summary.failed.append((r["id"], str(e)))
            continue
        db.mark(r["id"], posted_at=datetime.now().isoformat(timespec="seconds"),
                odoo_move_id=out.move_id, post_error=None)
        (summary.existing if out.status == "exists" else summary.posted).append((r["id"], out.ref))
    return summary
