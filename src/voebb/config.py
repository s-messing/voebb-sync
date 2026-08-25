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


def derive_account(user: str) -> str:
    """A stable namespace for an account, without exposing the card number.

    Calendar UIDs travel to every synced device, so the library card number
    itself must not go in them.
    """
    return hashlib.sha1(user.encode()).hexdigest()[:8] if user else "default"


DEFAULT_CALENDAR_NAME = "VÖBB Leihfristen"
DEFAULT_ALARM_DAYS = 3


@dataclass(frozen=True)
class NextcloudConfig:
    """Where to write loan reminders, and how far ahead to warn."""

    url: str
    user: str
    app_password: str
    calendar_name: str = DEFAULT_CALENDAR_NAME
    alarm_days: int = DEFAULT_ALARM_DAYS
    account: str = "default"

    def __repr__(self) -> str:  # keep secrets out of tracebacks and logs
        return (
            f"NextcloudConfig(url={self.url!r}, user={self.user!r}, "
            f"app_password=***, calendar_name={self.calendar_name!r}, "
            f"alarm_days={self.alarm_days}, account={self.account!r})"
        )


def load_nextcloud_config() -> NextcloudConfig:
    """Read the Nextcloud CalDAV settings from .env or the environment."""
    load_dotenv()
    url = os.getenv("NEXTCLOUD_URL", "").strip().rstrip("/")
    user = os.getenv("NEXTCLOUD_USER", "").strip()
    app_password = os.getenv("NEXTCLOUD_APP_PASSWORD", "")
    if not url or not user or not app_password:
        raise SystemExit(
            "NEXTCLOUD_URL, NEXTCLOUD_USER and NEXTCLOUD_APP_PASSWORD must be set.\n"
            "See .env.example. Create the app password under "
            "Nextcloud > Settings > Security > Create new app password."
        )

    raw_days = os.getenv("VOEBB_ALARM_DAYS", "").strip()
    try:
        alarm_days = int(raw_days) if raw_days else DEFAULT_ALARM_DAYS
    except ValueError as exc:
        raise SystemExit(f"VOEBB_ALARM_DAYS must be a whole number, got {raw_days!r}") from exc
    if alarm_days < 0:
        raise SystemExit(f"VOEBB_ALARM_DAYS must not be negative, got {alarm_days}")

    label = os.getenv("VOEBB_ACCOUNT", "").strip()
    account = account_slug(label) if label else derive_account(os.getenv("VOEBB_USER", "").strip())

    return NextcloudConfig(
        url=url,
        user=user,
        app_password=app_password,
        calendar_name=os.getenv("VOEBB_CALENDAR_NAME", "").strip() or DEFAULT_CALENDAR_NAME,
        alarm_days=alarm_days,
        account=account,
    )
