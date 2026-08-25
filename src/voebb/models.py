"""Plain data returned by the client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Loan:
    """One currently borrowed item."""

    title: str
    library: str
    due_date: date | None
    note: str = ""
    renewals: int | None = None
    media_type: str = ""
    shelf_mark: str = ""
    item_number: str = ""

    @property
    def days_left(self) -> int | None:
        if self.due_date is None:
            return None
        return (self.due_date - date.today()).days


@dataclass(frozen=True)
class SearchResult:
    """One hit from the catalogue."""

    position: int
    title: str
    author: str = ""
    year: str = ""
    media_type: str = ""
