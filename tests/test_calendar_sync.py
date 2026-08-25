"""Calendar sync tests. Pure logic only - no CalDAV server involved."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from icalendar import Calendar

from voebb.calendar_sync import (
    UID_PREFIX,
    build_event,
    dav_root,
    event_signature,
    loan_uid,
    plan_sync,
)
from voebb.models import Loan

STAMP = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def make_loan(**overrides) -> Loan:
    defaults = dict(
        title="Der Prozess",
        library="Musterbezirk: Stadtteilbibliothek",
        due_date=date(2026, 9, 8),
        note="2 Verlängerungen",
        renewals=2,
        media_type="",
        shelf_mark="Rom Kafk",
        item_number="01456136631",
    )
    return Loan(**{**defaults, **overrides})


def vevent(ical: bytes):
    return next(iter(Calendar.from_ical(ical).walk("VEVENT")))


class TestUid:
    def test_uses_the_item_barcode(self):
        assert loan_uid(make_loan()) == f"{UID_PREFIX}01456136631@voebb.local"

    def test_survives_a_renewal(self):
        """A renewal moves the due date; the UID must not move with it."""
        before = loan_uid(make_loan(due_date=date(2026, 9, 8), renewals=2))
        after = loan_uid(make_loan(due_date=date(2026, 10, 6), renewals=3))
        assert before == after

    def test_distinct_items_get_distinct_uids(self):
        assert loan_uid(make_loan(item_number="111")) != loan_uid(make_loan(item_number="222"))

    def test_falls_back_to_a_hash_without_a_barcode(self):
        uid = loan_uid(make_loan(item_number=""))
        assert uid.startswith(UID_PREFIX) and uid.endswith("@voebb.local")

    def test_fallback_is_stable_across_renewals_too(self):
        a = loan_uid(make_loan(item_number="", due_date=date(2026, 9, 8), renewals=1))
        b = loan_uid(make_loan(item_number="", due_date=date(2027, 1, 1), renewals=4))
        assert a == b


class TestBuildEvent:
    def test_is_an_all_day_event(self):
        """date, not datetime - otherwise Nextcloud renders a timed event."""
        assert vevent(build_event(make_loan(), alarm_days=3)).decoded("dtstart") == date(2026, 9, 8)

    def test_dtend_is_exclusive(self):
        assert vevent(build_event(make_loan(), alarm_days=3)).decoded("dtend") == date(2026, 9, 9)

    def test_serialises_dtstart_as_a_date_value(self):
        assert b"DTSTART;VALUE=DATE:20260908" in build_event(make_loan(), alarm_days=3)

    def test_alarm_fires_the_configured_number_of_days_early(self):
        assert b"TRIGGER:-P5D" in build_event(make_loan(), alarm_days=5)

    def test_alarm_has_no_attachment(self):
        """Nextcloud's validator has rejected VALARMs carrying a sound."""
        assert b"ATTACH" not in build_event(make_loan(), alarm_days=3)

    def test_carries_prodid_and_version(self):
        ical = build_event(make_loan(), alarm_days=3)
        assert b"PRODID" in ical and b"VERSION:2.0" in ical

    def test_summary_and_location(self):
        event = vevent(build_event(make_loan(), alarm_days=3))
        assert str(event["summary"]) == "Rückgabe: Der Prozess"
        assert str(event["location"]) == "Musterbezirk: Stadtteilbibliothek"

    def test_description_carries_the_details(self):
        description = str(vevent(build_event(make_loan(), alarm_days=3))["description"])
        assert "Rom Kafk" in description
        assert "01456136631" in description
        assert "Verlängerungen: 2" in description

    def test_refuses_a_loan_without_a_due_date(self):
        with pytest.raises(ValueError):
            build_event(make_loan(due_date=None), alarm_days=3)


class TestSignature:
    def test_ignores_dtstamp(self):
        """Otherwise every run would look like an update."""
        early = build_event(make_loan(), alarm_days=3, stamp=STAMP)
        later = build_event(make_loan(), alarm_days=3, stamp=datetime(2027, 1, 1, tzinfo=timezone.utc))
        assert event_signature(early) == event_signature(later)

    def test_notices_a_moved_due_date(self):
        a = build_event(make_loan(), alarm_days=3)
        b = build_event(make_loan(due_date=date(2026, 10, 6)), alarm_days=3)
        assert event_signature(a) != event_signature(b)

    def test_notices_a_changed_alarm(self):
        a = build_event(make_loan(), alarm_days=3)
        b = build_event(make_loan(), alarm_days=7)
        assert event_signature(a) != event_signature(b)


class TestPlanSync:
    def test_creates_events_for_a_fresh_calendar(self):
        plan = plan_sync([make_loan()], {}, alarm_days=3)
        assert list(plan.create) == [loan_uid(make_loan())]
        assert not plan.update and not plan.delete

    def test_second_run_changes_nothing(self):
        """The core property: syncing twice must not duplicate or churn."""
        loan = make_loan()
        first = plan_sync([loan], {}, alarm_days=3)
        existing = dict(first.create)
        second = plan_sync([loan], existing, alarm_days=3)
        assert second.is_empty
        assert second.unchanged == [loan_uid(loan)]

    def test_renewal_updates_in_place_rather_than_duplicating(self):
        existing = dict(plan_sync([make_loan()], {}, alarm_days=3).create)
        renewed = make_loan(due_date=date(2026, 10, 6), renewals=3)
        plan = plan_sync([renewed], existing, alarm_days=3)
        assert list(plan.update) == [loan_uid(renewed)]
        assert not plan.create and not plan.delete

    def test_returned_item_is_deleted(self):
        existing = dict(plan_sync([make_loan()], {}, alarm_days=3).create)
        plan = plan_sync([], existing, alarm_days=3)
        assert plan.delete == [loan_uid(make_loan())]

    def test_loan_without_due_date_is_skipped_not_crashed(self):
        plan = plan_sync([make_loan(due_date=None)], {}, alarm_days=3)
        assert plan.skipped == ["Der Prozess"]
        assert plan.is_empty

    def test_mixed_round(self):
        keep, going = make_loan(item_number="111"), make_loan(item_number="222")
        existing = dict(plan_sync([keep, going], {}, alarm_days=3).create)
        arriving = make_loan(item_number="333")
        moved = make_loan(item_number="111", due_date=date(2026, 12, 1))
        plan = plan_sync([moved, arriving], existing, alarm_days=3)
        assert list(plan.create) == [loan_uid(arriving)]
        assert list(plan.update) == [loan_uid(moved)]
        assert plan.delete == [loan_uid(going)]

    def test_summary_reads_naturally(self):
        assert "1 angelegt" in plan_sync([make_loan()], {}, alarm_days=3).summary()


@pytest.mark.parametrize(
    "given,expected",
    [
        ("https://cloud.example.de", "https://cloud.example.de/remote.php/dav"),
        ("https://cloud.example.de/", "https://cloud.example.de/remote.php/dav"),
        ("https://cloud.example.de/remote.php/dav", "https://cloud.example.de/remote.php/dav"),
        ("https://cloud.example.de/remote.php/dav/", "https://cloud.example.de/remote.php/dav"),
    ],
)
def test_dav_root_accepts_bare_host_or_full_path(given, expected):
    assert dav_root(given) == expected
