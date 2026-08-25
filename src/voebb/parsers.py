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


def _lines(node: Tag | None) -> list[str]:
    """Split a table cell on <br> into its logical lines.

    The title cell packs up to four facts into one <td>, separated only by
    <br>: an optional [media type], the title, the shelf mark and the item
    barcode. get_text() would weld them into one string.
    """
    if node is None:
        return []
    lines: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if joined := " ".join(current).strip():
            lines.append(joined.replace("\xa0", " "))
        current.clear()

    for child in node.children:
        if getattr(child, "name", None) == "br":
            flush()
        elif isinstance(child, Tag):
            current.append(child.get_text(" ", strip=True))
        else:
            current.append(str(child).strip())
    flush()
    return lines


def _split_title_cell(node: Tag | None) -> tuple[str, str, str, str]:
    """Pull (media_type, title, shelf_mark, item_number) out of the title cell.

    Everything except the title is optional, so parts are claimed from the
    ends inward rather than by fixed position.
    """
    parts = _lines(node)
    media_type = ""

    if parts:
        if match := _MEDIA_PREFIX.match(parts[0]):
            media_type = match.group("kind")
            remainder = parts[0][match.end():].strip()
            parts = ([remainder] if remainder else []) + parts[1:]

    item_number = ""
    if parts and parts[-1].isdigit():
        item_number = parts[-1]
        parts = parts[:-1]

    # Only treat a trailing line as the shelf mark if something is left for
    # the title - a single-line cell is a title, not a shelf mark.
    shelf_mark = ""
    if len(parts) > 1:
        shelf_mark = parts[-1]
        parts = parts[:-1]

    return media_type, " ".join(parts).strip(), shelf_mark, item_number


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

        media_type, title, shelf_mark, item_number = _split_title_cell(cells[i_title])

        note = cell(i_note)
        renewals = int(m.group(1)) if (m := _RENEWALS.search(note)) else None

        loans.append(
            Loan(
                title=title,
                library=cell(i_lib),
                due_date=parse_german_date(cell(i_due)),
                note=note,
                renewals=renewals,
                media_type=media_type,
                shelf_mark=shelf_mark,
                item_number=item_number,
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

