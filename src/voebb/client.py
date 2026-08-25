"""High-level, read-only client for a VOEBB account."""

from __future__ import annotations

from bs4 import BeautifulSoup

from .config import Credentials, load_credentials
from .models import Loan, SearchResult
from .parsers import parse_loans, parse_search_results
from .session import AdisError, AdisSession, SessionExpired

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

    def __enter__(self) -> "VoebbClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        """Log out politely so the server drops the session."""
        if self._logged_in:
            try:
                self.session.submit({"$Button$2": "Abmelden"})
            except Exception:
                pass  # best effort; never mask the real error
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
            raise AdisError(
                "expected the login form after opening 'Mein Konto', "
                f"got {self.session.url}"
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

    def loans(self) -> list[Loan]:
        """Currently borrowed items with their due dates."""
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

    def search(self, query: str) -> list[SearchResult]:
        """Search the catalogue. Does not require an account.

        Returns the first page of hits; the server-rendered result list carries
        no total count and no pagination controls, so this is all the site gives
        us without JavaScript.
        """
        self._ensure_search_box()
        page: BeautifulSoup = self.session.submit(
            {"$Autosuggest": query, "$Button": "Suchen"}
        )
        return parse_search_results(page)

    def _ensure_search_box(self) -> None:
        """Inner pages such as 'Meine Ausleihen' drop the search box.

        Posting a query from one of those silently yields no hits, so move back
        to a page that carries it first - without throwing away the login.
        """
        if self.session.form is None:
            self.session.start()
        if "$Autosuggest" in self.session.form.fields:
            return
        if self._logged_in:
            self.session.navigate(KONTO)
        else:
            self.session.start()
        if self.session.form is None or "$Autosuggest" not in self.session.form.fields:
            raise AdisError(f"no search box reachable from {self.session.url}")
