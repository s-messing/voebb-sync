"""What an unattended run reports when the far end fails.

The timer reads the exit code and the journal reads stderr, so both matter
more here than they do interactively.
"""

from __future__ import annotations

import niquests
import pytest
import requests
from caldav.lib.error import AuthorizationError, DAVError

from voebb import cli
from voebb.session import AdisError, SessionExpired


def _raising(exc: BaseException):
    def func(args):
        raise exc

    return func


@pytest.mark.parametrize(
    "exc",
    [
        AdisError("no form on https://www.voebb.de/aDISWeb/app/prod00"),
        SessionExpired("session expired while opening loans"),
        DAVError("calendar server said no"),
        AuthorizationError(),
        requests.ConnectionError("voebb.de refused the connection"),
        niquests.ConnectTimeout("cloud.example.de timed out"),
    ],
)
def test_expected_failures_exit_1_without_a_traceback(exc, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_cmd_sync_calendar", _raising(exc))

    assert cli.main(["sync-calendar"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("Fehler: ")
    assert captured.err.strip() != "Fehler:"
    assert "Traceback" not in captured.err


def test_a_bug_still_raises(monkeypatch):
    """Only the far end is caught. A TypeError here is ours, and must surface."""
    monkeypatch.setattr(cli, "_cmd_sync_calendar", _raising(TypeError("this one is a bug")))

    with pytest.raises(TypeError):
        cli.main(["sync-calendar"])


def test_reason_never_reports_an_empty_cause():
    # requests and niquests both stringify a bare transport error to "", which
    # would otherwise put a naked "Fehler:" in the journal.
    assert cli._reason(requests.ConnectionError()) == "ConnectionError"
    assert cli._reason(niquests.ConnectTimeout()) == "ConnectTimeout"
    assert cli._reason(AdisError("  spaced  ")) == "spaced"


def test_json_before_sync_calendar_is_rejected(capsys):
    """--json parses at the top level, but sync-calendar has no JSON output.
    Refusing beats silently printing text."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--json", "sync-calendar"])
    assert excinfo.value.code == 2
    assert "--json" in capsys.readouterr().err


def test_json_after_sync_calendar_is_rejected():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["sync-calendar", "--json"])
    assert excinfo.value.code == 2
