"""Mirror current loans into a CalDAV calendar as all-day reminder events.

Split deliberately in two:

* `plan_sync` is pure - it diffs desired events against what is already there
  and returns what would change. All the interesting logic lives here and is
  testable without a server.
* `sync` does the CalDAV I/O and applies that plan.

Idempotency rests on a stable UID per item. The library's own 11-digit barcode
is used, because it survives renewals (which move the due date) and so lets a
renewal *move* an event rather than create a second one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import caldav
from caldav.lib.error import NotFoundError
from icalendar import Alarm, Calendar, Event

from .config import NextcloudConfig
from .models import Loan

UID_PREFIX = "voebb-"
UID_DOMAIN = "@voebb.local"
PRODID = "-//voebb//loan reminders//DE"


def loan_uid(loan: Loan) -> str:
    """A UID that stays the same for as long as the item is on loan."""
    if loan.item_number:
        identity = loan.item_number
    else:
        # No barcode on the row: degrade to a hash of the fields that do not
        # change while the item is out. Never include due_date or renewals.
        key = f"{loan.title}|{loan.library}|{loan.media_type}".encode()
        identity = hashlib.sha1(key).hexdigest()[:16]
    return f"{UID_PREFIX}{identity}{UID_DOMAIN}"


def build_event(loan: Loan, *, alarm_days: int, stamp: datetime | None = None) -> bytes:
    """Render one loan as a VCALENDAR containing a single all-day VEVENT."""
    if loan.due_date is None:
        raise ValueError(f"loan without a due date cannot be scheduled: {loan.title!r}")

    calendar = Calendar()
    # sabre/dav rejects a PUT without these two.
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")

    event = Event()
    event.add("uid", loan_uid(loan))
    event.add("summary", f"Rückgabe: {loan.title}")
    event.add("location", loan.library)
    event.add("description", _describe(loan))
    # date (not datetime) makes icalendar emit DTSTART;VALUE=DATE, i.e. all-day.
    event.add("dtstart", loan.due_date)
    # DTEND is exclusive, so a single-day event ends the following day.
    event.add("dtend", loan.due_date + timedelta(days=1))
    event.add("dtstamp", stamp or datetime.now(timezone.utc))
    event.add("transp", "TRANSPARENT")

    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", f"Bald fällig: {loan.title}")
    # Bare timedelta -> TRIGGER:-P3D, relative to DTSTART.
    # No ATTACH: Nextcloud's CalDAV validator has rejected alarms with sounds.
    alarm.add("trigger", timedelta(days=-alarm_days))
    event.add_component(alarm)

    calendar.add_component(event)
    return calendar.to_ical()


def _describe(loan: Loan) -> str:
    lines = [loan.library]
    if loan.shelf_mark:
        lines.append(f"Signatur: {loan.shelf_mark}")
    if loan.item_number:
        lines.append(f"Mediennummer: {loan.item_number}")
    if loan.media_type:
        lines.append(f"Medienart: {loan.media_type}")
    if loan.renewals is not None:
        lines.append(f"Verlängerungen: {loan.renewals}")
    if loan.note:
        lines.append(loan.note)
    return "\n".join(lines)


def event_signature(ical: bytes) -> tuple:
    """The parts of an event we actually care about keeping in sync.

    Compared instead of the raw bytes because DTSTAMP changes on every build
    and would make every run look like an update.
    """
    calendar = Calendar.from_ical(ical)
    for component in calendar.walk("VEVENT"):
        alarms = tuple(
            str(alarm.get("trigger").dt) for alarm in component.walk("VALARM")
            if alarm.get("trigger") is not None
        )
        return (
            str(component.get("summary", "")),
            str(component.get("description", "")),
            str(component.get("location", "")),
            str(component.decoded("dtstart")),
            str(component.decoded("dtend")) if component.get("dtend") else "",
            alarms,
        )
    raise ValueError("no VEVENT in calendar data")


@dataclass
class SyncPlan:
    """What a sync would do. Values are the iCal payloads to write."""

    create: dict[str, bytes] = field(default_factory=dict)
    update: dict[str, bytes] = field(default_factory=dict)
    delete: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    calendar_missing: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.create or self.update or self.delete)

    def summary(self) -> str:
        parts = [
            f"{len(self.create)} angelegt",
            f"{len(self.update)} aktualisiert",
            f"{len(self.delete)} entfernt",
            f"{len(self.unchanged)} unverändert",
        ]
        if self.skipped:
            parts.append(f"{len(self.skipped)} übersprungen")
        return ", ".join(parts)


def plan_sync(
    loans: list[Loan], existing: dict[str, bytes], *, alarm_days: int
) -> SyncPlan:
    """Diff desired events against what the calendar already holds.

    `existing` maps UID -> iCal payload, and must contain only events this tool
    owns; deleting anything else is not this function's business.
    """
    plan = SyncPlan()
    desired: dict[str, bytes] = {}

    for loan in loans:
        if loan.due_date is None:
            plan.skipped.append(loan.title)
            continue
        uid = loan_uid(loan)
        desired[uid] = build_event(loan, alarm_days=alarm_days)

    for uid, ical in desired.items():
        current = existing.get(uid)
        if current is None:
            plan.create[uid] = ical
        elif event_signature(current) != event_signature(ical):
            plan.update[uid] = ical
        else:
            plan.unchanged.append(uid)

    plan.delete = [uid for uid in existing if uid not in desired]
    return plan


def open_calendar(config: NextcloudConfig, *, create: bool = True) -> caldav.Calendar | None:
    """Find the configured calendar.

    With `create=False` a missing calendar yields None instead of being
    created, so that a dry run stays genuinely read-only.
    """
    client = caldav.DAVClient(
        url=dav_root(config.url), username=config.user, password=config.app_password
    )
    principal = client.get_principal()
    for calendar in principal.get_calendars():
        if _display_name(calendar) == config.calendar_name:
            return calendar
    return principal.make_calendar(name=config.calendar_name) if create else None


def dav_root(url: str) -> str:
    """Accept either a bare Nextcloud host or a full DAV root."""
    url = url.rstrip("/")
    return url if "/remote.php" in url else f"{url}/remote.php/dav"


def _display_name(calendar: caldav.Calendar) -> str:
    try:
        properties = calendar.get_properties([caldav.elements.dav.DisplayName()])
        return str(properties.get("{DAV:}displayname", "") or "")
    except Exception:
        return str(getattr(calendar, "name", "") or "")


def fetch_managed_events(calendar: caldav.Calendar) -> tuple[dict[str, bytes], dict]:
    """Return only the events this tool owns, keyed by UID.

    Events without our UID prefix are ignored entirely, so the target calendar
    can be one you also use for other things.
    """
    payloads: dict[str, bytes] = {}
    objects: dict = {}
    for event in calendar.get_events():
        data = event.data
        if isinstance(data, str):
            data = data.encode()
        uid = _uid_of(data)
        if uid and uid.startswith(UID_PREFIX):
            payloads[uid] = data
            objects[uid] = event
    return payloads, objects


def _uid_of(ical: bytes) -> str | None:
    for component in Calendar.from_ical(ical).walk("VEVENT"):
        uid = component.get("uid")
        return str(uid) if uid else None
    return None


def sync(loans: list[Loan], config: NextcloudConfig, *, dry_run: bool = False) -> SyncPlan:
    """Reconcile the calendar against the current loan list."""
    calendar = open_calendar(config, create=not dry_run)
    if calendar is None:
        # Dry run against a calendar that does not exist yet.
        existing, objects = {}, {}
        calendar_missing = True
    else:
        existing, objects = fetch_managed_events(calendar)
        calendar_missing = False

    plan = plan_sync(loans, existing, alarm_days=config.alarm_days)
    plan.calendar_missing = calendar_missing

    if dry_run:
        return plan

    for ical in plan.create.values():
        calendar.add_event(ical)

    for uid, ical in plan.update.items():
        event = objects[uid]
        event.data = ical.decode()
        event.save()

    for uid in plan.delete:
        objects[uid].delete()

    return plan
