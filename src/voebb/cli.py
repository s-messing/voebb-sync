"""Command line entry point: `voebb-cli loans` / `voebb search "<query>"`."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

import niquests
import requests
from caldav.lib.error import DAVError

from .calendar_sync import loan_uid, sync
from .client import VoebbClient
from .config import load_caldav_config
from .models import Format
from .session import AdisError

# Everything a run can fail on that is not a bug: voebb.de (AdisError), the
# calendar server (DAVError), and the two independent HTTP stacks underneath
# them - requests for aDIS, niquests for CalDAV.
FAILURES = (AdisError, DAVError, requests.RequestException, niquests.RequestException)


def _reason(exc: BaseException) -> str:
    """A one-liner for the journal. Bare transport errors stringify empty."""
    return str(exc).strip() or exc.__class__.__name__


def _emit_json(rows: list) -> None:
    print(
        json.dumps([dataclasses.asdict(r) for r in rows], default=str, ensure_ascii=False, indent=2)
    )


def _cmd_loans(args: argparse.Namespace) -> int:
    with VoebbClient() as client:
        loans = client.loans()
    if getattr(args, "json", False):
        _emit_json(loans)
        return 0
    if not loans:
        print("Keine Ausleihen.")
        return 0
    width = max(len(loan.title) for loan in loans)
    for loan in sorted(loans, key=lambda item: (item.due_date is None, item.due_date)):
        due = loan.due_date.strftime("%d.%m.%Y") if loan.due_date else "?"
        days = loan.days_left
        urgency = f"({days:+d} Tage)" if days is not None else ""
        print(f"{due} {urgency:>12}  {loan.title:<{width}}  {loan.library}")
    return 0


def _format_arg(value: str) -> list[Format]:
    """One --format occurrence: a member name, or a comma list of them.

    Names are matched case-insensitively and '-' counts as '_', so the
    natural spellings ``--format dvd,blu-ray`` work. Returning a list per
    occurrence lets argparse's ``extend`` action flatten repeated flags and
    comma lists into one list[Format].
    """
    formats = []
    for token in value.split(","):
        if not (token := token.strip()):
            continue
        try:
            formats.append(Format[token.upper().replace("-", "_")])
        except KeyError:
            names = ", ".join(f.name for f in Format)
            raise argparse.ArgumentTypeError(f"unknown format {token!r}; one of: {names}") from None
    return formats


def _cmd_search(args: argparse.Namespace) -> int:
    with VoebbClient() as client:
        results = client.search(args.query, formats=args.format)
    if getattr(args, "json", False):
        _emit_json(results)
        return 0
    if not results:
        print("Keine Treffer.")
        return 0
    shown = results[: args.limit]
    for result in shown:
        year = f" ({result.year})" if result.year else ""
        print(f"{result.position:>3}. {result.title}{year}")
        if result.author:
            print(f"     {result.author}")
    if len(results) > len(shown):
        print(f"\n... {len(shown)} von {len(results)} Treffern auf dieser Seite.")
    return 0


def _cmd_sync_calendar(args: argparse.Namespace) -> int:
    config = load_caldav_config()
    if args.calendar:
        config = dataclasses.replace(config, calendar_name=args.calendar)
    if args.alarm_days is not None:
        config = dataclasses.replace(config, alarm_days=args.alarm_days)

    with VoebbClient() as client:
        loans = client.loans()

    titles = {loan_uid(loan, config.account): loan.title for loan in loans}
    plan = sync(loans, config, dry_run=args.dry_run)

    for uid, label in (("+", "create"), ("~", "update")):
        for event_uid in getattr(plan, label):
            print(f"{uid} {titles.get(event_uid, event_uid)}")
    # A delete usually means the item came back, but it can also be an event
    # written under an older UID scheme being replaced. Tell them apart by the
    # item number, so a migration is not reported as a return.
    held = {loan.item_number for loan in loans if loan.item_number}
    for event_uid in plan.delete:
        migrated = any(
            f"-{number}@" in event_uid or event_uid.startswith(f"voebb-{number}@")
            for number in held
        )
        reason = "ersetzt" if migrated else "zurückgegeben"
        print(f"- {titles.get(event_uid, event_uid)}   ({reason})")
    for title in plan.skipped:
        print(f"? {title}   (kein Fälligkeitsdatum, übersprungen)")

    where = f"{config.calendar_name!r} auf {config.url}"
    if args.dry_run:
        if plan.calendar_missing:
            print(
                f"! Kalender {config.calendar_name!r} existiert noch nicht "
                f"und würde angelegt werden."
            )
        print(f"\nProbelauf für {where} - nichts geändert.")
        print(plan.summary())
    elif plan.is_empty:
        print(f"{where}: bereits aktuell ({plan.summary()}).")
    else:
        print(f"\n{where}: {plan.summary()}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Shared so --json is accepted both before and after the subcommand.
    # SUPPRESS matters: with a normal default the subparser would write
    # json=False over a --json given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="emit JSON instead of text",
    )

    parser = argparse.ArgumentParser(
        prog="voebb-cli", description="Read a VOEBB library account.", parents=[common]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    loans = sub.add_parser("loans", help="show borrowed items and due dates", parents=[common])
    loans.set_defaults(func=_cmd_loans)

    search = sub.add_parser("search", help="search the catalogue", parents=[common])
    search.add_argument("query")
    search.add_argument("-n", "--limit", type=int, default=10, help="results to show (default 10)")
    search.add_argument(
        "-f",
        "--format",
        action="extend",
        type=_format_arg,
        default=None,
        metavar="FORMAT[,FORMAT]",
        help="only these media formats, e.g. --format dvd --format blu_ray "
        "or --format dvd,blu_ray (case-insensitive; a wrong name lists all)",
    )
    search.set_defaults(func=_cmd_search)

    sync_cal = sub.add_parser(
        "sync-calendar",
        help="mirror loans into a CalDAV calendar as reminders",
    )
    sync_cal.add_argument(
        "--dry-run", action="store_true", help="show what would change, change nothing"
    )
    sync_cal.add_argument("--calendar", help="override the target calendar name")
    sync_cal.add_argument(
        "--alarm-days", type=int, help="days before the due date to fire the reminder"
    )
    sync_cal.set_defaults(func=_cmd_sync_calendar)

    args = parser.parse_args(argv)
    # --json before the subcommand parses fine even for subcommands that have
    # no JSON output; refuse it rather than silently printing text.
    if getattr(args, "json", False) and args.func is _cmd_sync_calendar:
        parser.error("sync-calendar does not support --json")
    try:
        return args.func(args)
    except FAILURES as exc:
        # Unattended runs land in a journal, where a one-line cause is worth
        # more than a traceback.
        print(f"Fehler: {_reason(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
