"""Command line entry point: `voebb loans` / `voebb search "<query>"`."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from .client import VoebbClient
from .session import AdisError


def _emit_json(rows: list) -> None:
    print(json.dumps([dataclasses.asdict(r) for r in rows], default=str, ensure_ascii=False, indent=2))


def _cmd_loans(args: argparse.Namespace) -> int:
    with VoebbClient() as client:
        loans = client.loans()
    if args.json:
        _emit_json(loans)
        return 0
    if not loans:
        print("Keine Ausleihen.")
        return 0
    width = max(len(loan.title) for loan in loans)
    for loan in sorted(loans, key=lambda l: (l.due_date is None, l.due_date)):
        due = loan.due_date.strftime("%d.%m.%Y") if loan.due_date else "?"
        days = loan.days_left
        urgency = f"({days:+d} Tage)" if days is not None else ""
        print(f"{due} {urgency:>12}  {loan.title:<{width}}  {loan.library}")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    with VoebbClient() as client:
        results = client.search(args.query)
    if args.json:
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


def main(argv: list[str] | None = None) -> int:
    # Shared so --json is accepted both before and after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit JSON instead of text")

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

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AdisError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
