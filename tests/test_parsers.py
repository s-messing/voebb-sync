"""Parser tests against saved pages. No network."""

from __future__ import annotations

import pathlib
from datetime import date

import pytest
from bs4 import BeautifulSoup

from voebb.parsers import ParseError, parse_german_date, parse_loans, parse_search_results

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def soup(name: str) -> BeautifulSoup:
    return BeautifulSoup((FIXTURES / name).read_text(encoding="utf-8"), "lxml")


@pytest.fixture(scope="module")
def loans():
    return parse_loans(soup("loans.html"))


def test_parses_every_row(loans):
    assert len(loans) == 2


def test_reads_due_date(loans):
    assert all(loan.due_date == date(2026, 9, 8) for loan in loans)


def test_reads_library(loans):
    assert loans[0].library == "Musterbezirk: Stadtteilbibliothek Beispielstraße"


def test_splits_media_type_off_the_title(loans):
    assert loans[0].media_type == "Gerät (Laptop u.a.)"
    assert loans[0].title.startswith("Beispieltitel")
    assert "[" not in loans[0].title


def test_splits_the_title_cell_into_its_parts(loans):
    """The cell packs title, shelf mark and barcode behind <br> separators."""
    assert loans[0].shelf_mark == "Kinderhörbuch Must"
    assert loans[0].item_number == "00000000001"
    assert "00000000001" not in loans[0].title
    assert "Kinderhörbuch" not in loans[0].title


def test_item_numbers_are_distinct_per_row(loans):
    """They become calendar UIDs, so collisions would silently merge events."""
    assert loans[0].item_number != loans[1].item_number


def test_row_without_a_media_prefix_still_splits(loans):
    assert loans[1].media_type == ""
    assert loans[1].shelf_mark == "5.2/Comic Beis"
    assert loans[1].item_number == "00000000002"
    assert loans[1].title == "Beispielreihe 1/2. - 4."


def test_single_line_title_cell_is_all_title():
    """A cell with no <br> must not have its only line taken as a shelf mark."""
    html = """<table class="rTable_table"><tr><th>Fällig am</th><th>Bibliothek</th>
    <th>Titel</th><th>Hinweis</th></tr><tr><td>01.01.2027</td><td>Zweigstelle</td>
    <td>Nur ein Titel</td><td></td></tr></table>"""
    loan = parse_loans(BeautifulSoup(html, "lxml"))[0]
    assert loan.title == "Nur ein Titel"
    assert loan.shelf_mark == "" and loan.item_number == ""


def test_counts_renewals_from_the_hinweis_column(loans):
    assert loans[0].renewals == 3
    assert loans[1].renewals == 2


def test_days_left_is_relative_to_today(loans):
    assert loans[0].days_left == (date(2026, 9, 8) - date.today()).days


def test_empty_account_is_not_an_error():
    assert parse_loans(soup("loans_empty.html")) == []


def test_unrecognisable_page_raises():
    with pytest.raises(ParseError):
        parse_loans(BeautifulSoup("<html><body><p>Hoppla</p></body></html>", "lxml"))


def test_column_lookup_survives_reordering():
    """Columns are found by header text, not position."""
    html = (FIXTURES / "loans.html").read_text(encoding="utf-8")
    reordered = (
        html.replace("Fällig am", "ZZZ").replace("Titel", "Fällig am").replace("ZZZ", "Titel")
    )
    parsed = parse_loans(BeautifulSoup(reordered, "lxml"))
    # Headers swapped, so the parser should now read the date cell as the title.
    assert parsed[0].title == "08.09.2026"


@pytest.fixture(scope="module")
def results():
    return parse_search_results(soup("search.html"))


class TestSearch:
    def test_parses_every_hit(self, results):
        assert len(results) == 3

    def test_reads_title_and_year(self, results):
        assert results[1].title == "Franz Kafka: Die Verwandlung"
        assert results[1].year == "2024"

    def test_reads_author_skipping_the_empty_spacer(self, results):
        assert results[1].author.startswith("Franz Kafka ; Sven Görtz")

    def test_positions_are_sequential(self, results):
        assert [r.position for r in results] == [1, 2, 3]

    def test_no_hits_is_empty_not_an_error(self):
        assert parse_search_results(BeautifulSoup("<html><body></body></html>", "lxml")) == []


@pytest.mark.parametrize(
    "value,expected",
    [
        ("08.09.2026", date(2026, 9, 8)),
        ("fällig am 01.01.2027", date(2027, 1, 1)),
        ("", None),
        ("demnächst", None),
    ],
)
def test_german_dates(value, expected):
    assert parse_german_date(value) == expected
