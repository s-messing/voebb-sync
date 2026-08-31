"""Credential loading. Values are read from the environment and never logged."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Credentials:
    user: str
    password: str

    def __repr__(self) -> str:  # keep secrets out of tracebacks and logs
        return f"Credentials(user={self.user[:2]}***, password=***)"


def load_credentials() -> Credentials:
    """Read VOEBB_USER / VOEBB_PASSWORD from .env or the environment."""
    load_dotenv()
    user = os.getenv("VOEBB_USER", "").strip()
    password = os.getenv("VOEBB_PASSWORD", "")
    if not user or not password:
        raise SystemExit(
            "VOEBB_USER and VOEBB_PASSWORD must be set.\n"
            "Copy .env.example to .env and fill in your library card number and password."
        )
    return Credentials(user=user, password=password)


def account_slug(label: str) -> str:
    """Normalise an account label so it is safe inside a calendar UID."""
    return re.sub(r"[^a-z0-9]", "", label.lower()) or "default"


def derive_account(user: str, salt: str = "") -> str:
    """A stable namespace for an account, without exposing the card number.

    Calendar UIDs travel to every synced device, so the library card number
    itself must not go in them - and an 11-digit card number alone hashes
    into a space small enough to brute-force, so the hash is salted with
    something a stranger holding a UID does not also hold.
    """
    if not user:
        return "default"
    return hashlib.sha1(f"{salt}:{user}".encode()).hexdigest()[:8]


def dav_root(url: str) -> str:
    """Complete a bare Nextcloud host to its DAV root.

    Applied only to NEXTCLOUD_URL - CALDAV_URL is taken verbatim, because on
    any other server there is nothing sensible to append.
    """
    url = url.rstrip("/")
    return url if "/remote.php" in url else f"{url}/remote.php/dav"


DEFAULT_CALENDAR_NAME = "VÖBB Leihfristen"
DEFAULT_ALARM_DAYS = 3


@dataclass(frozen=True)
class CaldavConfig:
    """Where to write loan reminders, and how far ahead to warn.

    `url` is the full DAV root, already resolved by `load_caldav_config`.
    """

    url: str
    user: str
    password: str
    calendar_name: str = DEFAULT_CALENDAR_NAME
    alarm_days: int = DEFAULT_ALARM_DAYS
    account: str = "default"

    def __repr__(self) -> str:  # keep secrets out of tracebacks and logs
        return (
            f"CaldavConfig(url={self.url!r}, user={self.user!r}, "
            f"password=***, calendar_name={self.calendar_name!r}, "
            f"alarm_days={self.alarm_days}, account={self.account!r})"
        )


def load_caldav_config() -> CaldavConfig:
    """Read the CalDAV settings from .env or the environment.

    Both spellings work: CALDAV_* names any server and is used verbatim,
    NEXTCLOUD_* additionally accepts a bare host and completes it to
    Nextcloud's /remote.php/dav. CALDAV_* wins where both are set.
    """
    load_dotenv()
    caldav_url = os.getenv("CALDAV_URL", "").strip().rstrip("/")
    nextcloud_url = os.getenv("NEXTCLOUD_URL", "").strip().rstrip("/")
    url = caldav_url or (dav_root(nextcloud_url) if nextcloud_url else "")
    user = os.getenv("CALDAV_USER", "").strip() or os.getenv("NEXTCLOUD_USER", "").strip()
    password = os.getenv("CALDAV_PASSWORD", "") or os.getenv("NEXTCLOUD_APP_PASSWORD", "")
    if not url or not user or not password:
        raise SystemExit(
            "CALDAV_URL, CALDAV_USER and CALDAV_PASSWORD must be set\n"
            "(or, for Nextcloud, NEXTCLOUD_URL / NEXTCLOUD_USER / NEXTCLOUD_APP_PASSWORD).\n"
            "See .env.example. On Nextcloud, create an app password under "
            "Settings > Security > Create new app password."
        )

    raw_days = os.getenv("VOEBB_ALARM_DAYS", "").strip()
    try:
        alarm_days = int(raw_days) if raw_days else DEFAULT_ALARM_DAYS
    except ValueError as exc:
        raise SystemExit(f"VOEBB_ALARM_DAYS must be a whole number, got {raw_days!r}") from exc
    if alarm_days < 0:
        raise SystemExit(f"VOEBB_ALARM_DAYS must not be negative, got {alarm_days}")

    label = os.getenv("VOEBB_ACCOUNT", "").strip()
    account = (
        account_slug(label)
        if label
        # Salted with the Nextcloud user: stable, always set, and not part
        # of the UIDs that sync out.
        else derive_account(os.getenv("VOEBB_USER", "").strip(), salt=user)
    )

    return CaldavConfig(
        url=url,
        user=user,
        password=password,
        calendar_name=os.getenv("VOEBB_CALENDAR_NAME", "").strip() or DEFAULT_CALENDAR_NAME,
        alarm_days=alarm_days,
        account=account,
    )
