"""The aDIS/BMS form protocol.

www.voebb.de runs aDIS/BMS, an Apache-Tapestry-style app where *all* state lives
on the server. There are no meaningful URLs: every page holds a single
``<form name="Form0">``, and every interaction is a POST of that entire form with
one extra control telling the server what was clicked.

Three things rotate on every single response and must never be hardcoded:
the ``_sid`` path segment in the form action, the per-page ``identity`` token,
and ``requestCount``. ``_absorb`` re-reads all of them after each request, which
is the invariant the whole client rests on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://www.voebb.de"
START_URL = f"{BASE_URL}/aDISWeb/app/prod00"

# Navigation targets are pushed through the form's `selected` field as a
# 12-char fixed-width record: "ZTEXT" padded to 12, then the screen code.
# Mirrors htmlOnLink() in /aDISWeb/js/aDISMain.min.js.
_SELECT_PREFIX = "ZTEXT".ljust(12)

# Transport-level retry, so one dropped connection during an unattended run
# does not cost a whole day's reminders.
RETRY_TOTAL = 3
RETRY_BACKOFF = 1.0
RETRY_STATUSES = (429, 500, 502, 503, 504)


def build_retry(total: int = RETRY_TOTAL, backoff: float = RETRY_BACKOFF) -> Retry:
    """Retry policy shared by the aDIS and CalDAV transports."""
    return Retry(
        total=total,
        connect=total,
        read=total,
        status=total,
        backoff_factor=backoff,
        status_forcelist=RETRY_STATUSES,
        # Every aDIS interaction is a POST - even plain navigation - so the
        # default "idempotent methods only" policy would retry nothing at all.
        # Safe here because this client never mutates the library account: the
        # worst a replayed navigation POST can do is return an unexpected page.
        allowed_methods=None,
        raise_on_status=False,
        respect_retry_after_header=True,
    )


def retrying_adapter(total: int = RETRY_TOTAL) -> HTTPAdapter:
    return HTTPAdapter(max_retries=build_retry(total))


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _attr(tag: Tag, name: str, default: str = "") -> str:
    """Read an attribute as a plain string.

    BeautifulSoup returns a list for multi-valued attributes (class, rel), so
    a bare .get() is not guaranteed to be a str.
    """
    value = tag.get(name, default)
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return default if value is None else str(value)


class AdisError(RuntimeError):
    """The site did something we don't understand."""


class SessionExpired(AdisError):
    """The server dropped us back to the start page."""


@dataclass
class FormState:
    """The parsed ``Form0`` of the page we are currently sitting on."""

    action: str
    fields: dict[str, str] = field(default_factory=dict)


class AdisSession:
    """Drives the aDIS form state machine one click at a time."""

    def __init__(
        self, *, delay: float = 0.4, timeout: float = 30.0, retries: int = RETRY_TOTAL
    ) -> None:
        self.http = requests.Session()
        # requests implements RFC 6265 path-scoped cookies, which the `_sid`
        # cookie (Path=/aDISWeb/_<sid>) depends on. Hand-rolled cookie headers
        # break the session here.
        self.http.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        adapter = retrying_adapter(retries)
        self.http.mount("https://", adapter)
        self.http.mount("http://", adapter)

        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.page: BeautifulSoup | None = None
        self.form: FormState | None = None
        self.url: str | None = None

    # -- plumbing ---------------------------------------------------------

    def _absorb(self, response: requests.Response) -> BeautifulSoup:
        """Make the response the current page and re-read its form state."""
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        form = soup.find("form", attrs={"name": "Form0"}) or soup.find("form")
        if form is None:
            raise AdisError(f"no form on {response.url}")

        fields: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = _attr(inp, "name")
            if not name:
                continue
            kind = (_attr(inp, "type") or "text").lower()
            if kind in ("submit", "button", "image", "reset"):
                continue  # only sent when actually clicked
            if kind in ("checkbox", "radio") and not inp.has_attr("checked"):
                continue
            fields[name] = _attr(inp, "value")
        for sel in form.find_all("select"):
            name = _attr(sel, "name")
            if not name:
                continue
            chosen = sel.find("option", selected=True) or sel.find("option")
            fields[name] = _attr(chosen, "value") if isinstance(chosen, Tag) else ""

        self.page = soup
        self.url = response.url
        self.form = FormState(action=urljoin(response.url, _attr(form, "action")), fields=fields)
        return soup

    def start(self) -> BeautifulSoup:
        """Open a fresh session on the start page."""
        return self._absorb(self.http.get(START_URL, timeout=self.timeout))

    def submit(self, controls: dict[str, str | list[str]] | None = None) -> BeautifulSoup:
        """POST the current form plus the clicked control; follow the 303.

        A list value is sent as a repeated field, the way the browser submits
        several hidden inputs of the same name (e.g. the filter tree's
        checked boxes).
        """
        if self.form is None:
            raise AdisError("submit() before start()")
        data: dict[str, str | list[str]] = dict(self.form.fields)
        data.update(controls or {})
        time.sleep(self.delay)  # a public library service; one request at a time
        response = self.http.post(
            self.form.action,
            data=data,
            headers={"Referer": self.url or START_URL},
            timeout=self.timeout,
            allow_redirects=True,
        )
        return self._absorb(response)

    # -- navigation -------------------------------------------------------

    def navigate(self, code: str) -> BeautifulSoup:
        """Follow a JS-wired nav link, e.g. ``*SBK`` for Mein Konto."""
        return self.submit({"keyCode": "0", "selected": _SELECT_PREFIX + code})

    def click_field(self, fld: str) -> BeautifulSoup:
        """Click an in-page link addressed by its ``fld`` / ``data-fld`` id."""
        if self.page is None:
            raise AdisError("click_field() before start()")
        link = self.page.select_one(f'[fld="{fld}"], [data-fld="{fld}"]')
        if link is None:
            raise AdisError(f"no element with fld={fld!r} on {self.url}")
        if link.name == "input":  # a real submit button: send its name/value
            return self.submit({_attr(link, "name", fld): _attr(link, "value")})
        return self.submit({"keyCode": "0", "selected": _SELECT_PREFIX + fld})

    def at_start_page(self) -> bool:
        """True if the server bounced us back to the unauthenticated start page."""
        if self.page is None:
            return False
        title = self.page.title.get_text(strip=True) if self.page.title else ""
        return title.startswith("Startseite")
