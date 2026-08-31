"""Read-only programmatic access to a VOEBB library account."""

from .calendar_sync import SyncPlan, build_event, loan_uid, plan_sync, sync
from .client import VoebbAuthError, VoebbClient
from .config import (
    CaldavConfig,
    Credentials,
    load_caldav_config,
    load_credentials,
)
from .models import Loan, SearchResult
from .session import AdisError, AdisSession, SessionExpired

__all__ = [
    "VoebbClient",
    "VoebbAuthError",
    "Credentials",
    "CaldavConfig",
    "load_credentials",
    "load_caldav_config",
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
