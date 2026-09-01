"""High-level, read-only client for a VOEBB account."""

from __future__ import annotations

import contextlib
from collections.abc import Iterable

from bs4 import BeautifulSoup, Tag

from .config import Credentials, load_credentials
from .models import Format, Loan, SearchResult
from .parsers import format_checkbox_ids, parse_loans, parse_search_results
from .session import AdisError, AdisSession, SessionExpired, _attr

# Screen codes, as passed to htmlOnLink() by the site's own JS.
KONTO = "*SBK"
AUSLEIHEN = "*SZA"


class VoebbAuthError(AdisError):
    """The card number / password was rejected."""


class VoebbClient:
    """Log in once, then read the account.

    Usage::

        with VoebbClient() as client:
            for loan in client.loans():
                print(loan.due_date, loan.title)
    """

    def __init__(self, credentials: Credentials | None = None, **session_kwargs) -> None:
        self._credentials = credentials
        self.session = AdisSession(**session_kwargs)
        self._logged_in = False

    def __enter__(self) -> VoebbClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        """Log out politely so the server drops the session."""
        if self._logged_in:
            # Best effort; logging out must never mask the real error.
            with contextlib.suppress(Exception):
                self.session.submit({"$Button$2": "Abmelden"})
            self._logged_in = False
        self.session.http.close()

    # -- account ----------------------------------------------------------

    def login(self) -> None:
        """Authenticate via the OIDC login form behind 'Mein Konto'."""
        if self._logged_in:
            return
        credentials = self._credentials or load_credentials()

        self.session.start()
        self.session.navigate(KONTO)

        form = self.session.form
        if form is None or "LPASSW" not in form.fields:
            if self.session.at_start_page():
                raise SessionExpired(
                    f"bounced back to the start page instead of the login form ({self.session.url})"
                )
            raise AdisError(
                f"expected the login form after opening 'Mein Konto', got {self.session.url}"
            )

        page = self.session.submit(
            {
                "L#AUSW": credentials.user,
                "LPASSW": credentials.password,
                "LLOGIN": "Anmelden",
            }
        )

        # A rejected login re-renders the login form instead of the account.
        if self.session.form and "LPASSW" in self.session.form.fields:
            raise VoebbAuthError(
                "login rejected - check VOEBB_USER (library card number) and VOEBB_PASSWORD"
            )
        if "Mein Konto" not in (page.title.get_text(strip=True) if page.title else ""):
            raise AdisError(f"unexpected page after login: {self.session.url}")
        self._logged_in = True

    def _reset_session(self) -> None:
        """Throw the session away so the next call logs in from scratch."""
        stale = self.session
        self.session = AdisSession(
            delay=stale.delay,
            timeout=stale.timeout,
            retries=stale.retries,
        )
        self._logged_in = False
        stale.http.close()

    def loans(self) -> list[Loan]:
        """Currently borrowed items with their due dates.

        aDIS sessions expire on their own schedule, and the server signals it
        by silently bouncing us to the start page rather than erroring. For an
        unattended daily run that would mean a skipped day, so an expired
        session is rebuilt and retried once.
        """
        try:
            return self._loans()
        except SessionExpired:
            self._reset_session()
            return self._loans()

    def _loans(self) -> list[Loan]:
        self.login()
        # The loans screen is only reachable from the account overview - from a
        # search result page the *SZA code goes nowhere.
        self.session.navigate(KONTO)
        page = self.session.navigate(AUSLEIHEN)
        if self.session.at_start_page():
            raise SessionExpired("session expired while opening loans")
        title = page.title.get_text(strip=True) if page.title else ""
        if not title.startswith("Meine Ausleihen"):
            raise AdisError(f"expected the loans page, got {title!r} at {self.session.url}")
        return parse_loans(page)

    # -- catalogue --------------------------------------------------------

    def search(self, query: str, formats: Iterable[Format] | None = None) -> list[SearchResult]:
        """Search the catalogue. Does not require an account.

        Returns the first page of hits; the server-rendered result list carries
        no total count and no pagination controls, so this is all the site gives
        us without JavaScript.

        ``formats`` narrows the hits to items in any of the given formats, the
        way the browser UI does it: tick the formats' boxes in the result
        list's "Medienart" filter tree, then press "Filtern". The tree only
        lists formats that occur in the current result set, so a format
        without hits is simply dropped - and if none of the requested formats
        has hits, the empty list is returned without a second request.
        """
        self._ensure_search_box()
        page: BeautifulSoup = self.session.submit({"$Autosuggest": query, "$Button": "Suchen"})
        wanted = list(formats or [])
        if not wanted:
            return parse_search_results(page)

        boxes = format_checkbox_ids(page, wanted)
        if not boxes:
            return []
        # Checked facet boxes travel as one repeated field: $CbTree_text, one
        # value per checkbox id (requests encodes a list as repeated fields),
        # mirroring the hidden inputs mjsInitCbTree() appends in the site's
        # own JS. The Filtern button's positional $Button$n name shifts
        # between pages, so read it off the page instead of hardcoding it.
        filter_button = page.find("input", attrs={"value": "Filtern"})
        if not isinstance(filter_button, Tag):
            raise AdisError(
                f"result list has a filter tree but no Filtern button ({self.session.url})"
            )
        page = self.session.submit({"$CbTree_text": boxes, _attr(filter_button, "name"): "Filtern"})
        return parse_search_results(page)

    def _ensure_search_box(self) -> None:
        """Inner pages such as 'Meine Ausleihen' drop the search box.

        Posting a query from one of those silently yields no hits, so move back
        to a page that carries it first - without throwing away the login.
        """
        if self.session.form is None:
            self.session.start()
        if self.session.form is None:
            raise AdisError("could not open a session")
        if "$Autosuggest" in self.session.form.fields:
            return
        if self._logged_in:
            self.session.navigate(KONTO)
        else:
            self.session.start()
        if self.session.form is None or "$Autosuggest" not in self.session.form.fields:
            raise AdisError(f"no search box reachable from {self.session.url}")
