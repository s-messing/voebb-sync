"""Command line entry point: `voebb loans` / `voebb search "<query>"`."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from .calendar_sync import loan_uid, sync
from .client import VoebbClient
from .config import load_nextcloud_config
from .session import AdisError


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


def _cmd_search(args: argparse.Namespace) -> int:
    with VoebbClient() as client:
        results = client.search(args.query)
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
    config = load_nextcloud_config()
    if args.calendar:
        config = dataclasses.replace(config, calendar_name=args.calendar)
    if args.alarm_days is not None:
        config = dataclasses.replace(config, alarm_days=args.alarm_days)

    with VoebbClient() as client:
        loans = client.loans()

    titles = {loan_uid(loan): loan.title for loan in loans}
    plan = sync(loans, config, dry_run=args.dry_run)

    for uid, label in (("+", "create"), ("~", "update")):
        for event_uid in getattr(plan, label):
            print(f"{uid} {titles.get(event_uid, event_uid)}")
    for event_uid in plan.delete:
        print(f"- {titles.get(event_uid, event_uid)}   (zurückgegeben)")
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
        prog="voebb", description="Read a VOEBB library account.", parents=[common]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    loans = sub.add_parser("loans", help="show borrowed items and due dates", parents=[common])
    loans.set_defaults(func=_cmd_loans)

    search = sub.add_parser("search", help="search the catalogue", parents=[common])
    search.add_argument("query")
    search.add_argument("-n", "--limit", type=int, default=10, help="results to show (default 10)")
    search.set_defaults(func=_cmd_search)

    sync_cal = sub.add_parser(
        "sync-calendar",
        help="mirror loans into the Nextcloud calendar as reminders",
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
    try:
        return args.func(args)
    except AdisError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
