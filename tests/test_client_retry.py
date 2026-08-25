"""Session-expiry recovery. No network: the session is a stub."""

from __future__ import annotations

import pytest

from voebb.client import VoebbClient
from voebb.config import Credentials
from voebb.models import Loan
from voebb.session import SessionExpired

CREDENTIALS = Credentials(user="123", password="secret")


class FakeSession:
    """Stands in for AdisSession, counting how often it is driven."""

    def __init__(self, delay: float = 0.0, timeout: float = 0.0, retries: int = 3) -> None:
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.http = type("Http", (), {"close": lambda self: None})()


@pytest.fixture
def client(monkeypatch) -> VoebbClient:
    monkeypatch.setattr("voebb.client.AdisSession", FakeSession)
    return VoebbClient(CREDENTIALS)


def test_expired_session_is_retried_once(client, monkeypatch):
    calls = []
    loan = Loan(title="Der Prozess", library="Zweigstelle", due_date=None)

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise SessionExpired("expired")
        return [loan]

    monkeypatch.setattr(client, "_loans", flaky)
    assert client.loans() == [loan]
    assert len(calls) == 2, "should have retried exactly once"


def test_retry_builds_a_fresh_session(client, monkeypatch):
    """A stale session must be discarded, not reused."""
    first = client.session
    client._logged_in = True
    seen = []

    def flaky():
        seen.append(client.session)
        if len(seen) == 1:
            raise SessionExpired("expired")
        return []

    monkeypatch.setattr(client, "_loans", flaky)
    client.loans()
    assert seen[1] is not first
    assert client.session is not first


def test_retry_clears_the_logged_in_flag(client, monkeypatch):
    client._logged_in = True
    states = []

    def flaky():
        states.append(client._logged_in)
        if len(states) == 1:
            raise SessionExpired("expired")
        return []

    monkeypatch.setattr(client, "_loans", flaky)
    client.loans()
    assert states == [True, False], "second attempt must start logged out"


def test_gives_up_after_one_retry(client, monkeypatch):
    """Persistent expiry must surface, not spin forever."""
    calls = []

    def always_expired():
        calls.append(1)
        raise SessionExpired("expired")

    monkeypatch.setattr(client, "_loans", always_expired)
    with pytest.raises(SessionExpired):
        client.loans()
    assert len(calls) == 2


def test_other_errors_are_not_retried(client, monkeypatch):
    """A real failure should fail fast rather than doubling the load."""
    calls = []

    def broken():
        calls.append(1)
        raise ValueError("something else")

    monkeypatch.setattr(client, "_loans", broken)
    with pytest.raises(ValueError):
        client.loans()
    assert len(calls) == 1


def test_happy_path_does_not_retry(client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "_loans", lambda: calls.append(1) or [])
    client.loans()
    assert len(calls) == 1
