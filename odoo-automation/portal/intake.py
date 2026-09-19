"""Night-audit packs arriving by e-mail instead of being uploaded.

A hotel's PMS (or its GM) sends the pack to one address; this reads the message, saves it,
and runs each attachment through the same pipeline an upload uses.  Nothing downstream knows
the difference -- a run created here looks exactly like a run created on the upload page.

Sources, in the same shape as the storage and database adapters:

    FolderSource   .eml files (or bare PDFs) dropped in a directory.  No account, no DNS, no
                   credentials: this is how the whole path is tested, and how a batch of old
                   reports can be replayed.
    webhook        POST /intake/mail, a transactional mail provider handing over the same
                   fields (see `message_from_postmark`).  `Message` is the seam, so the
                   provider can be changed without the pipeline noticing.

De-duplication is three layers deep because any one of them has a hole:

    Message-Id     catches a plain re-delivery.  Forwarding usually mints a new one, so on its
                   own it misses the most common duplicate of all.
    content hash   the real one: the same attachment bytes are the same report, no matter who
                   forwarded it or how many times.
    PMS-CODE-DATE  already in the pipeline -- a genuine re-run with corrections has different
                   bytes but the same reference, so it supersedes rather than doubling up.
"""
from __future__ import annotations

import email
import hashlib
import shutil
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator, Optional

from . import clock

#: what we will read out of a message.  Anything else is ignored rather than refused: a signature
#: image or a logo is not an error, it is just not a night-audit pack.
REPORT_SUFFIXES = (".pdf", ".txt", ".csv", ".xlsx", ".xlsm")


@dataclass
class Attachment:
    name: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class Message:
    """One arriving e-mail, however it reached us."""
    message_id: str
    sender: str = ""
    recipient: str = ""
    subject: str = ""
    received_at: Optional[datetime] = None
    attachments: list[Attachment] = field(default_factory=list)
    raw: bytes = b""

    @property
    def reports(self) -> list[Attachment]:
        return [a for a in self.attachments if Path(a.name).suffix.lower() in REPORT_SUFFIXES]


def _clean(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name or "attachment").name) or "attachment"


def message_from_bytes(raw: bytes, fallback_id: str = "") -> Message:
    """Parse an RFC-822 message.  Used by the folder source and by the webhook when the provider
    hands over the original message rather than parsed fields."""
    parsed: EmailMessage = email.message_from_bytes(raw, _class=EmailMessage)
    received = None
    if parsed.get("Date"):
        try:
            received = parsedate_to_datetime(parsed["Date"])
        except (TypeError, ValueError):       # a malformed Date header is not worth refusing over
            received = None
    msg = Message(
        message_id=(parsed.get("Message-Id") or "").strip("<> ") or fallback_id or _digest(raw),
        sender=parsed.get("From", ""), recipient=parsed.get("To", ""),
        subject=parsed.get("Subject", ""), received_at=received, raw=raw)
    for part in parsed.walk():
        if part.get_content_maintype() == "multipart":
            continue
        name = part.get_filename()
        if not name:
            continue
        data = part.get_payload(decode=True)
        if data:
            msg.attachments.append(Attachment(_clean(name), data))
    return msg


def message_from_postmark(payload: dict) -> Message:
    """The JSON an inbound mail provider POSTs for one message.

    Written against Postmark's shape because it needs no domain to start with -- mail to
    `<guid>@inbound.postmarkapp.com` arrives here, and moving to the hotels' own domain later
    changes the address, not this code.  Anything unexpected in the payload is ignored rather
    than refused: a message we half understand is still evidence worth keeping.
    """
    import base64
    from_full = payload.get("FromFull") or {}
    raw_date = payload.get("Date") or ""
    received = None
    if raw_date:
        try:
            received = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            received = None
    msg = Message(
        message_id=(payload.get("MessageID") or "").strip("<> ") or _digest(repr(payload).encode()),
        sender=from_full.get("Email") or payload.get("From", ""),
        recipient=payload.get("OriginalRecipient") or payload.get("To", ""),
        subject=payload.get("Subject", ""), received_at=received)
    for att in payload.get("Attachments") or []:
        name, content = att.get("Name"), att.get("Content")
        if not name or not content:
            continue
        try:
            data = base64.b64decode(content)
        except Exception:            # noqa: BLE001 - a corrupt attachment is not a reason to
            continue                 # drop the whole message; the rest may be fine
        msg.attachments.append(Attachment(_clean(name), data))
    return msg


def message_from_file(path: Path) -> Message:
    """A bare report dropped in the folder, with no envelope around it.  Treated as a message
    carrying one attachment, so replaying old files uses exactly the same path as real mail."""
    data = path.read_bytes()
    return Message(message_id=f"file:{_digest(data)}", subject=path.name,
                   received_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                   attachments=[Attachment(_clean(path.name), data)], raw=b"")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


class FolderSource:
    """Messages from a directory.  `.eml` files are parsed as mail; anything else is taken as a
    bare report.  Files are left where they are -- the de-duplication decides what is new, so a
    folder can be re-scanned safely and a half-finished run can simply be run again."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def messages(self) -> Iterator[Message]:
        if not self.folder.exists():
            return
        for path in sorted(self.folder.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() == ".eml":
                yield message_from_bytes(path.read_bytes(), fallback_id=f"file:{path.name}")
            elif path.suffix.lower() in REPORT_SUFFIXES:
                yield message_from_file(path)

    def describe(self) -> str:
        return f"folder {self.folder}"


# ------------------------------------------------------------------ taking a message in
@dataclass
class IntakeResult:
    message: Message
    created: list = field(default_factory=list)      # (run_id, RunResult) for each new report
    duplicates: list[str] = field(default_factory=list)   # attachment names already seen
    ignored: list[str] = field(default_factory=list)      # attachments that are not reports

    @property
    def did_nothing(self) -> bool:
        return not self.created


def ingest(msg: Message, *, db, storage, props, property_code: str = "",
           allowed: Optional[set] = None) -> IntakeResult:
    """Save an arriving message and run its reports through the pipeline.

    Every attachment is kept whether or not it parses: the file is the evidence behind the
    entry, and an attachment we could not read is the thing somebody will want to look at.
    """
    from pms_to_odoo.pipeline import process_file      # imported late: keeps intake importable
                                                        # without the parser stack (webhook tests)
    res = IntakeResult(message=msg)
    received = msg.received_at.isoformat(timespec="seconds") if msg.received_at else None
    # The folder is a name somebody reads in a file listing, so it carries the time the
    # mail arrived as it reads in Oklahoma, whatever offset the sending server stamped it with.
    stamp = (clock.to_local(msg.received_at) or clock.now()).strftime("%Y%m%d-%H%M%S")

    reports = msg.reports
    res.ignored = [a.name for a in msg.attachments if a not in reports]

    # One folder per message, not per attachment: a format that sends two halves (SynXis) needs
    # both files side by side for the reader to find the second one.
    folder = f"{stamp}-{hashlib.sha256(msg.message_id.encode()).hexdigest()[:8]}"
    fresh: list[tuple[Attachment, Path, str]] = []
    for att in reports:
        if db.intake_seen(att.sha256):
            res.duplicates.append(att.name)             # same bytes: seen it, whoever sent it
            continue
        key = f"intake/{property_code or 'unsorted'}/{folder}/{att.name}"
        locator = storage.save(key, att.data)
        fresh.append((att, storage.local_path(locator), locator))

    # Parse after saving them all, so a pair that must be read together (SynXis sends the
    # revenue and the ledger halves as two reports) finds its other half in the same folder.
    consumed: set[Path] = set()
    for att, path, locator in fresh:
        if path in consumed:
            db.record_intake(sha256=att.sha256, message_id=msg.message_id, sender=msg.sender,
                             recipient=msg.recipient, subject=msg.subject, received_at=received,
                             file_name=att.name, stored_path=locator, run_id=None)
            continue
        out = process_file(path, props, property_code or None, allowed)
        out = _pair_with_earlier_half(out, path, db, storage, props, property_code, allowed)
        consumed.update(Path(c) for c in out.companions)
        run_id = db.add_run(
            uploaded_by=f"email:{msg.sender or 'unknown'}", property_code=out.property_code,
            business_date=out.business_date.isoformat() if out.business_date else None,
            pms=out.pms, ref=out.ref, status=out.status, message=out.message,
            file_name=att.name, stored_path=locator, result_json=out.to_json())
        db.record_intake(sha256=att.sha256, message_id=msg.message_id, sender=msg.sender,
                         recipient=msg.recipient, subject=msg.subject, received_at=received,
                         file_name=att.name, stored_path=locator, run_id=run_id)
        res.created.append((run_id, out))
    return res


def ingest_source(source, *, db, storage, props) -> list[IntakeResult]:
    """Drain a source.  Safe to run again: de-duplication decides what is new, so a re-scan of
    the same folder, or a webhook that delivers twice, changes nothing."""
    return [ingest(m, db=db, storage=storage, props=props) for m in source.messages()]


def _pair_with_earlier_half(out, path: Path, db, storage, props, property_code, allowed):
    """SynXis sends the revenue and the ledger halves as two reports, and on a schedule they
    arrive as two e-mails.  Neither half balances alone, so the first one to land waits; when
    the second arrives, the first is brought alongside it and the pair is read together.

    Symmetrical on purpose -- whichever half is second does the joining, because which one that
    is depends on the mail server, not on us.
    """
    from pms_to_odoo.pipeline import process_file
    if out.status != "awaiting_companion":
        return out
    earlier = db.awaiting_companion(out.property_code,
                                    out.business_date.isoformat() if out.business_date else None)
    if not earlier:
        return out                      # first half in: it waits, and says so on the daily list
    try:
        beside = path.parent / Path(earlier["file_name"]).name
        if not beside.exists():
            shutil.copy2(storage.local_path(earlier["stored_path"]), beside)
    except (OSError, FileNotFoundError):
        return out                      # the earlier file has gone; keep waiting rather than guess
    merged = process_file(path, props, property_code or None, allowed)
    # The merged run carries the same reference, so add_run supersedes the half that was waiting.
    return merged if merged.status != "awaiting_companion" else out
