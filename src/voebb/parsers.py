"""HTML -> models. Every VOEBB-specific selector in the project lives here.

Column lookup is header-driven rather than positional, so the loans table
survives aDIS reordering or inserting columns.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup, Tag

from .models import Loan, SearchResult

_RENEWALS = re.compile(r"(\d+)\s+Verlängerung")
_MEDIA_PREFIX = re.compile(r"^\[(?P<kind>[^\]]+)\]\s*")


class ParseError(RuntimeError):
    """The page didn't look like what we expected."""


def _text(node: Tag | None) -> str:
    return node.get_text(" ", strip=True).replace("\xa0", " ") if node else ""


def parse_german_date(value: str) -> date | None:
    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", value)
    if not match:
        return None
    return datetime.strptime(match.group(0), "%d.%m.%Y").date()


def parse_loans(soup: BeautifulSoup) -> list[Loan]:
    """Parse the 'Meine Ausleihen' table."""
    table = soup.select_one("table#resptable-1, table.rTable_table")
    if table is None:
        # An empty account renders prose instead of a table.
        if re.search(r"keine\s+(Ausleihen|Medien)", soup.get_text(" ", strip=True), re.I):
            return []
        raise ParseError("no loans table on page")

    rows = table.select("tr")
    if not rows:
        return []

    headers = [_text(th).lower() for th in rows[0].select("th")]
    if not headers:
        raise ParseError("loans table has no header row")

    def column(*names: str) -> int | None:
        for idx, head in enumerate(headers):
            if any(name in head for name in names):
                return idx
        return None

    i_due = column("fällig")
    i_lib = column("bibliothek")
    i_title = column("titel")
    i_note = column("hinweis")
    if i_title is None:
        raise ParseError(f"no title column in loans table; headers={headers}")

    loans: list[Loan] = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) <= i_title:
            continue

        def cell(idx: int | None) -> str:
            return _text(cells[idx]) if idx is not None and idx < len(cells) else ""

        raw_title = cell(i_title)
        media_type = ""
        if match := _MEDIA_PREFIX.match(raw_title):
            media_type = match.group("kind")
            raw_title = raw_title[match.end():]

        note = cell(i_note)
        renewals = int(m.group(1)) if (m := _RENEWALS.search(note)) else None

        loans.append(
            Loan(
                title=raw_title.strip(),
                library=cell(i_lib),
                due_date=parse_german_date(cell(i_due)),
                note=note,
                renewals=renewals,
                media_type=media_type,
            )
        )
    return loans


def parse_search_results(soup: BeautifulSoup) -> list[SearchResult]:
    """Parse a 'Trefferliste' result page."""
    items = soup.select("li.rList_li")
    results: list[SearchResult] = []
    for index, li in enumerate(items, start=1):
        title = _text(li.select_one(".rList_titel"))
        if not title:
            continue
        number = _text(li.select_one(".rList_num"))
        icon = li.select_one(".rList_medium img")
        results.append(
            SearchResult(
                position=int(number) if number.isdigit() else index,
                title=title,
                # The first .rList_name is a spacer; the populated one carries
                # the statement of responsibility.
                author=next(
                    (t for n in li.select(".rList_name") if (t := _text(n))), ""
                ),
                year=_text(li.select_one(".rList_jahr")),
                media_type=(icon.get("alt") or icon.get("title") or "") if icon else "",
            )
        )
    return results

