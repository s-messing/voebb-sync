"""Recognising the loans screen. No network: the session is a stub."""

from __future__ import annotations

import pathlib

import pytest
from bs4 import BeautifulSoup

from voebb.client import VoebbClient, screen_name
from voebb.config import Credentials
from voebb.session import AdisError, FormState

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# What the site has served since September 2026: the <title> is the bare site
# name on every screen, only the <h1> breadcrumb says where you are.
LOANS_PAGE = (FIXTURES / "loans_blank_row.html").read_text(encoding="utf-8")
OVERVIEW_PAGE = """
<html><head><title>Verbund der Öffentlichen Bibliotheken Berlins</title></head><body>
<form name="Form0" action="/aDISWeb/app">
<h1><span class="invisible">Aktuelle Seite: </span> Mein Konto - Übersicht</h1>
</form></body></html>
"""
# The shape before that: the screen name sat in the <title> and there was no
# breadcrumb heading.
LEGACY_LOANS_PAGE = """
<html><head><title>Meine Ausleihen - Verbund der Öffentlichen Bibliotheken Berlins</title></head>
<body><form name="Form0" action="/aDISWeb/app">
<table id="resptable-1"><tr><th>Fällig am</th><th>Titel</th><th>Hinweis</th></tr></table>
</form></body></html>
"""


class FakeSession:
    """Serves a fixed page per navigation target; the login is already done."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.page = None
        self.form = FormState(action="/aDISWeb/app")
        self.url = "https://www.voebb.de/aDISWeb/_x/app/prod00/3"
        self.http = type("Http", (), {"close": lambda self: None})()

    def navigate(self, code: str) -> BeautifulSoup:
        self.page = soup(self.pages[code])
        return self.page

    def at_start_page(self) -> bool:
        return False


def client_with(pages: dict[str, str]) -> VoebbClient:
    client = VoebbClient(Credentials(user="123", password="secret"))
    client.session = FakeSession(pages)  # ty: ignore[invalid-assignment]
    client._logged_in = True
    return client


def test_screen_name_reads_the_breadcrumb():
    assert screen_name(soup(LOANS_PAGE)) == "Mein Konto - Ausleihen"
    assert screen_name(soup(OVERVIEW_PAGE)) == "Mein Konto - Übersicht"
    assert screen_name(soup(LEGACY_LOANS_PAGE)) == ""


def test_loans_screen_is_recognised_by_its_heading():
    """The <title> no longer names the screen; the breadcrumb must do."""
    client = client_with({"*SBK": OVERVIEW_PAGE, "*SZA": LOANS_PAGE})
    assert client.loans() == []


def test_legacy_title_is_still_accepted():
    client = client_with({"*SBK": OVERVIEW_PAGE, "*SZA": LEGACY_LOANS_PAGE})
    assert client.loans() == []


def test_wrong_screen_is_reported_by_name():
    client = client_with({"*SBK": OVERVIEW_PAGE, "*SZA": OVERVIEW_PAGE})
    with pytest.raises(AdisError, match="Mein Konto - Übersicht"):
        client.loans()
