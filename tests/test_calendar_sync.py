"""Calendar sync tests. Pure logic only - no CalDAV server involved."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime

import pytest
from icalendar import Calendar

from voebb.calendar_sync import (
    UID_PREFIX,
    build_event,
    dav_root,
    durable_note,
    event_signature,
    loan_uid,
    namespace,
    owns,
    plan_sync,
)
from voebb.models import Loan

STAMP = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
ACCOUNT = "acct1"


def make_loan(**overrides) -> Loan:
    base = Loan(
        title="Der Prozess",
        library="Musterbezirk: Stadtteilbibliothek",
        due_date=date(2026, 9, 8),
        note="2 Verlängerungen",
        renewals=2,
        media_type="",
        shelf_mark="Rom Kafk",
        item_number="01456136631",
    )
    return dataclasses.replace(base, **overrides)


def vevent(ical: bytes):
    return next(iter(Calendar.from_ical(ical).walk("VEVENT")))


class TestUid:
    def test_uses_the_item_barcode(self):
        assert loan_uid(make_loan(), ACCOUNT) == f"{UID_PREFIX}{ACCOUNT}-01456136631@voebb.local"

    def test_survives_a_renewal(self):
        """A renewal moves the due date; the UID must not move with it."""
        before = loan_uid(make_loan(due_date=date(2026, 9, 8), renewals=2), ACCOUNT)
        after = loan_uid(make_loan(due_date=date(2026, 10, 6), renewals=3), ACCOUNT)
        assert before == after

    def test_distinct_items_get_distinct_uids(self):
        assert loan_uid(make_loan(item_number="111"), ACCOUNT) != loan_uid(
            make_loan(item_number="222"), ACCOUNT
        )

    def test_falls_back_to_a_hash_without_a_barcode(self):
        uid = loan_uid(make_loan(item_number=""), ACCOUNT)
        assert uid.startswith(UID_PREFIX) and uid.endswith("@voebb.local")

    def test_fallback_is_stable_across_renewals_too(self):
        a = loan_uid(make_loan(item_number="", due_date=date(2026, 9, 8), renewals=1), ACCOUNT)
        b = loan_uid(make_loan(item_number="", due_date=date(2027, 1, 1), renewals=4), ACCOUNT)
        assert a == b


class TestBuildEvent:
    def test_is_an_all_day_event(self):
        """date, not datetime - otherwise Nextcloud renders a timed event."""
        assert vevent(build_event(make_loan(), alarm_days=3, account=ACCOUNT)).decoded(
            "dtstart"
        ) == date(2026, 9, 8)

    def test_dtend_is_exclusive(self):
        assert vevent(build_event(make_loan(), alarm_days=3, account=ACCOUNT)).decoded(
            "dtend"
        ) == date(2026, 9, 9)

    def test_serialises_dtstart_as_a_date_value(self):
        assert b"DTSTART;VALUE=DATE:20260908" in build_event(
            make_loan(), alarm_days=3, account=ACCOUNT
        )

    def test_alarm_fires_the_configured_number_of_days_early(self):
        assert b"TRIGGER:-P5D" in build_event(make_loan(), alarm_days=5, account=ACCOUNT)

    def test_alarm_has_no_attachment(self):
        """Nextcloud's validator has rejected VALARMs carrying a sound."""
        assert b"ATTACH" not in build_event(make_loan(), alarm_days=3, account=ACCOUNT)

    def test_carries_prodid_and_version(self):
        ical = build_event(make_loan(), alarm_days=3, account=ACCOUNT)
        assert b"PRODID" in ical and b"VERSION:2.0" in ical

    def test_summary_and_location(self):
        event = vevent(build_event(make_loan(), alarm_days=3, account=ACCOUNT))
        assert str(event["summary"]) == "Rückgabe: Der Prozess"
        assert str(event["location"]) == "Musterbezirk: Stadtteilbibliothek"

    def test_description_carries_the_details(self):
        description = str(
            vevent(build_event(make_loan(), alarm_days=3, account=ACCOUNT))["description"]
        )
        assert "Rom Kafk" in description
        assert "01456136631" in description
        assert "Verlängerungen: 2" in description

    def test_refuses_a_loan_without_a_due_date(self):
        with pytest.raises(ValueError):
            build_event(make_loan(due_date=None), alarm_days=3, account=ACCOUNT)


class TestDurableNote:
    """The Hinweis column mixes durable facts with wording that goes stale."""

    @pytest.mark.parametrize(
        "note,expected",
        [
            # "Heute" is relative to the scrape date - wrong by tomorrow.
            ("Heute verlängert 3 Verlängerungen", ""),
            ("Heute verlängert", ""),
            # The count already has its own line in the description.
            ("2 Verlängerungen", ""),
            ("1 Verlängerung", ""),
            # Anything genuinely informative survives.
            ("nicht verlängerbar", "nicht verlängerbar"),
            ("noch nicht möglich 1 Verlängerung", "noch nicht möglich"),
            ("", ""),
        ],
    )
    def test_strips_only_the_stale_and_redundant_parts(self, note, expected):
        assert durable_note(note) == expected

    def test_description_has_no_relative_wording(self):
        loan = make_loan(note="Heute verlängert 3 Verlängerungen", renewals=3)
        description = str(vevent(build_event(loan, alarm_days=3, account=ACCOUNT))["description"])
        assert "Heute" not in description
        # ...but the renewal count is still there, structurally.
        assert "Verlängerungen: 3" in description

    def test_does_not_repeat_the_renewal_count(self):
        loan = make_loan(note="2 Verlängerungen", renewals=2)
        description = str(vevent(build_event(loan, alarm_days=3, account=ACCOUNT))["description"])
        assert description.count("Verlängerungen") == 1

    def test_durable_hint_reaches_the_description(self):
        loan = make_loan(note="nicht verlängerbar")
        assert "nicht verlängerbar" in str(
            vevent(build_event(loan, alarm_days=3, account=ACCOUNT))["description"]
        )

    def test_no_churn_when_the_site_drops_the_phrase(self):
        """Tomorrow the site stops saying "Heute verlängert". That must not
        register as a change and trigger a pointless calendar update."""
        today = make_loan(note="Heute verlängert 3 Verlängerungen", renewals=3)
        tomorrow = make_loan(note="3 Verlängerungen", renewals=3)
        assert event_signature(
            build_event(today, alarm_days=3, account=ACCOUNT)
        ) == event_signature(build_event(tomorrow, alarm_days=3, account=ACCOUNT))

    def test_plan_sees_no_update_when_only_the_phrase_changed(self):
        today = make_loan(note="Heute verlängert 3 Verlängerungen", renewals=3)
        existing = dict(plan_sync([today], {}, alarm_days=3, account=ACCOUNT).create)
        tomorrow = make_loan(note="3 Verlängerungen", renewals=3)
        assert plan_sync([tomorrow], existing, alarm_days=3, account=ACCOUNT).is_empty


class TestSignature:
    def test_ignores_dtstamp(self):
        """Otherwise every run would look like an update."""
        early = build_event(make_loan(), alarm_days=3, stamp=STAMP)
        later = build_event(make_loan(), alarm_days=3, stamp=datetime(2027, 1, 1, tzinfo=UTC))
        assert event_signature(early) == event_signature(later)

    def test_notices_a_moved_due_date(self):
        a = build_event(make_loan(), alarm_days=3, account=ACCOUNT)
        b = build_event(make_loan(due_date=date(2026, 10, 6)), alarm_days=3, account=ACCOUNT)
        assert event_signature(a) != event_signature(b)

    def test_notices_a_changed_alarm(self):
        a = build_event(make_loan(), alarm_days=3, account=ACCOUNT)
        b = build_event(make_loan(), alarm_days=7, account=ACCOUNT)
        assert event_signature(a) != event_signature(b)


class TestPlanSync:
    def test_creates_events_for_a_fresh_calendar(self):
        plan = plan_sync([make_loan()], {}, alarm_days=3, account=ACCOUNT)
        assert list(plan.create) == [loan_uid(make_loan(), ACCOUNT)]
        assert not plan.update and not plan.delete

    def test_second_run_changes_nothing(self):
        """The core property: syncing twice must not duplicate or churn."""
        loan = make_loan()
        first = plan_sync([loan], {}, alarm_days=3, account=ACCOUNT)
        existing = dict(first.create)
        second = plan_sync([loan], existing, alarm_days=3, account=ACCOUNT)
        assert second.is_empty
        assert second.unchanged == [loan_uid(loan, ACCOUNT)]

    def test_renewal_updates_in_place_rather_than_duplicating(self):
        existing = dict(plan_sync([make_loan()], {}, alarm_days=3, account=ACCOUNT).create)
        renewed = make_loan(due_date=date(2026, 10, 6), renewals=3)
        plan = plan_sync([renewed], existing, alarm_days=3, account=ACCOUNT)
        assert list(plan.update) == [loan_uid(renewed, ACCOUNT)]
        assert not plan.create and not plan.delete

    def test_returned_item_is_deleted(self):
        existing = dict(plan_sync([make_loan()], {}, alarm_days=3, account=ACCOUNT).create)
        plan = plan_sync([], existing, alarm_days=3, account=ACCOUNT)
        assert plan.delete == [loan_uid(make_loan(), ACCOUNT)]

    def test_loan_without_due_date_is_skipped_not_crashed(self):
        plan = plan_sync([make_loan(due_date=None)], {}, alarm_days=3, account=ACCOUNT)
        assert plan.skipped == ["Der Prozess"]
        assert plan.is_empty

    def test_mixed_round(self):
        keep, going = make_loan(item_number="111"), make_loan(item_number="222")
        existing = dict(plan_sync([keep, going], {}, alarm_days=3, account=ACCOUNT).create)
        arriving = make_loan(item_number="333")
        moved = make_loan(item_number="111", due_date=date(2026, 12, 1))
        plan = plan_sync([moved, arriving], existing, alarm_days=3, account=ACCOUNT)
        assert list(plan.create) == [loan_uid(arriving, ACCOUNT)]
        assert list(plan.update) == [loan_uid(moved, ACCOUNT)]
        assert plan.delete == [loan_uid(going, ACCOUNT)]

    def test_summary_reads_naturally(self):
        assert "1 angelegt" in plan_sync([make_loan()], {}, alarm_days=3, account=ACCOUNT).summary()


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


class TestAccountNamespacing:
    """Two family members must be able to share one calendar."""

    OTHER = "acct2"

    def test_same_item_under_two_accounts_gets_two_uids(self):
        loan = make_loan()
        assert loan_uid(loan, ACCOUNT) != loan_uid(loan, self.OTHER)

    def test_uid_carries_the_account_namespace(self):
        assert loan_uid(make_loan(), ACCOUNT).startswith(namespace(ACCOUNT))

    def test_account_owns_only_its_own_events(self):
        mine = loan_uid(make_loan(), ACCOUNT)
        theirs = loan_uid(make_loan(), self.OTHER)
        assert owns(mine, ACCOUNT)
        assert not owns(theirs, ACCOUNT)

    def test_prefix_is_not_confused_by_a_longer_account_name(self):
        """'a' must not claim events belonging to 'ab'."""
        assert not owns(loan_uid(make_loan(), "ab"), "a")
        assert not owns(loan_uid(make_loan(), "a"), "ab")

    def test_foreign_uids_are_never_owned(self):
        for uid in ("meeting@example.org", "voebbish@example.org", ""):
            assert not owns(uid, ACCOUNT)

    def test_shared_calendar_does_not_delete_the_other_account(self):
        """The regression this whole change exists to prevent."""
        alice = make_loan(item_number="111")
        bob = make_loan(item_number="222")

        calendar = dict(plan_sync([alice], {}, alarm_days=3, account=ACCOUNT).create)
        calendar.update(plan_sync([bob], {}, alarm_days=3, account=self.OTHER).create)
        assert len(calendar) == 2

        # Alice syncs again. Bob's event is not hers to touch, so it must not
        # even be visible to her plan.
        hers = {uid: data for uid, data in calendar.items() if owns(uid, ACCOUNT)}
        plan = plan_sync([alice], hers, alarm_days=3, account=ACCOUNT)
        assert plan.delete == []
        assert plan.is_empty
        assert loan_uid(bob, self.OTHER) in calendar

    def test_each_account_still_reconciles_its_own_returns(self):
        alice = make_loan(item_number="111")
        calendar = dict(plan_sync([alice], {}, alarm_days=3, account=ACCOUNT).create)
        hers = {uid: data for uid, data in calendar.items() if owns(uid, ACCOUNT)}
        plan = plan_sync([], hers, alarm_days=3, account=ACCOUNT)
        assert plan.delete == [loan_uid(alice, ACCOUNT)]


class TestLegacyUidMigration:
    """Events written before UIDs were namespaced must not be orphaned."""

    LEGACY = f"{UID_PREFIX}01456136631@voebb.local"

    def test_legacy_events_are_adopted(self):
        assert owns(self.LEGACY, ACCOUNT)

    def test_legacy_event_is_replaced_not_left_behind(self):
        loan = make_loan()
        existing = {self.LEGACY: build_event(loan, alarm_days=3, account=ACCOUNT)}
        plan = plan_sync([loan], existing, alarm_days=3, account=ACCOUNT)
        assert self.LEGACY in plan.delete
        assert loan_uid(loan, ACCOUNT) in plan.create


def test_dav_client_gets_a_timeout(monkeypatch):
    """caldav defaults to timeout=None; a stalled server must error, not hang
    until systemd's TimeoutStartSec kills the whole run."""
    import caldav

    from voebb.calendar_sync import open_calendar
    from voebb.config import NextcloudConfig

    seen = {}

    class Stop(Exception):
        pass

    class FakeSession:
        def mount(self, *args):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.session = FakeSession()

        def get_principal(self):
            raise Stop

    monkeypatch.setattr(caldav, "DAVClient", FakeClient)
    config = NextcloudConfig(url="https://cloud.example.de", user="u", app_password="p")
    with pytest.raises(Stop):
        open_calendar(config)
    assert seen.get("timeout"), "DAVClient must be given a finite timeout"
