"""Read-only programmatic access to a VOEBB library account."""

from .calendar_sync import SyncPlan, build_event, loan_uid, plan_sync, sync
from .client import VoebbAuthError, VoebbClient
from .config import (
    Credentials,
    NextcloudConfig,
    load_credentials,
    load_nextcloud_config,
)
from .models import Loan, SearchResult
from .session import AdisError, AdisSession, SessionExpired

__all__ = [
    "VoebbClient",
    "VoebbAuthError",
    "Credentials",
    "NextcloudConfig",
    "load_credentials",
    "load_nextcloud_config",
    "sync",
    "plan_sync",
    "build_event",
    "loan_uid",
    "SyncPlan",
    "Loan",
    "SearchResult",
    "AdisSession",
    "AdisError",
    "SessionExpired",
]
