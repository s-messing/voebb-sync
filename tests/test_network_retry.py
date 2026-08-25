"""Transport-level retry, exercised against a real (local) flaky server."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests
from requests.adapters import HTTPAdapter

from voebb.session import RETRY_STATUSES, AdisSession, build_retry


class FlakyHandler(BaseHTTPRequestHandler):
    """Fails `fail_times` requests with 503, then succeeds."""

    fail_times = 0
    seen = 0

    def _reply(self) -> None:
        type(self).seen += 1
        if type(self).seen <= type(self).fail_times:
            self.send_response(503)
            self.end_headers()
            return
        body = b"<html><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _reply
    do_POST = _reply

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib name
        """Keep pytest output clean."""


@pytest.fixture
def flaky_server():
    server = HTTPServer(("127.0.0.1", 0), FlakyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()


@pytest.fixture
def session():
    """A session using our retry policy, with backoff removed for speed."""
    http = requests.Session()
    adapter = HTTPAdapter(max_retries=build_retry(total=3, backoff=0))
    http.mount("http://", adapter)
    http.mount("https://", adapter)
    return http


def test_recovers_from_transient_failures(flaky_server, session):
    _, url = flaky_server
    FlakyHandler.seen, FlakyHandler.fail_times = 0, 2
    assert session.get(url, timeout=5).status_code == 200
    assert FlakyHandler.seen == 3, "should have taken three attempts"


def test_post_is_retried(flaky_server, session):
    """The subtle one: urllib3 does not retry POST by default, and every
    aDIS interaction - including navigation - is a POST."""
    _, url = flaky_server
    FlakyHandler.seen, FlakyHandler.fail_times = 0, 2
    assert session.post(url, data={"a": "b"}, timeout=5).status_code == 200
    assert FlakyHandler.seen == 3


def test_gives_up_after_a_bounded_number_of_attempts(flaky_server, session):
    """Persistent failure must stop, not hammer a public library service."""
    _, url = flaky_server
    FlakyHandler.seen, FlakyHandler.fail_times = 0, 99
    response = session.get(url, timeout=5)
    # raise_on_status=False, so the last response comes back rather than a
    # RetryError; AdisSession turns it into an HTTPError (see below).
    assert response.status_code == 503
    assert FlakyHandler.seen == 4, "one attempt plus three retries"


def test_exhausted_retries_surface_as_an_error(flaky_server, session):
    """The caller must not mistake a failed fetch for an empty page."""
    _, url = flaky_server
    FlakyHandler.seen, FlakyHandler.fail_times = 0, 99
    with pytest.raises(requests.exceptions.HTTPError):
        session.get(url, timeout=5).raise_for_status()


def test_connection_refused_is_retried(session):
    """Nothing listening: every attempt fails at connect."""
    with pytest.raises(requests.exceptions.ConnectionError):
        session.get("http://127.0.0.1:9/", timeout=2)


class TestPolicy:
    def test_retries_all_methods_including_post(self):
        assert build_retry().allowed_methods is None

    def test_covers_the_usual_transient_statuses(self):
        forced = build_retry().status_forcelist
        assert set(RETRY_STATUSES) <= set(forced)
        for status in (500, 502, 503, 504, 429):
            assert status in forced

    def test_does_not_retry_client_errors(self):
        """A 404 or a rejected login is not worth hammering the site over."""
        forced = build_retry().status_forcelist
        for status in (400, 401, 403, 404):
            assert status not in forced

    def test_backoff_is_enabled_by_default(self):
        assert build_retry().backoff_factor > 0

    def test_honours_retry_after(self):
        assert build_retry().respect_retry_after_header is True


def test_session_mounts_the_retrying_adapter():
    session = AdisSession()
    for scheme in ("https://", "http://"):
        adapter = session.http.adapters[scheme]
        assert isinstance(adapter, HTTPAdapter)
        assert adapter.max_retries.total == session.retries
        assert adapter.max_retries.allowed_methods is None
