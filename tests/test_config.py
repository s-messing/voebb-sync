"""Account namespace derivation. The UIDs built from it sync everywhere."""

from __future__ import annotations

import pytest

from voebb.config import account_slug, derive_account, load_caldav_config

NEXTCLOUD_ENV = {
    "NEXTCLOUD_URL": "https://cloud.example.de",
    "NEXTCLOUD_USER": "alice",
    "NEXTCLOUD_APP_PASSWORD": "app-pw",
    "VOEBB_USER": "10312345678",
    "VOEBB_ACCOUNT": "",
    "VOEBB_CALENDAR_NAME": "",
    "VOEBB_ALARM_DAYS": "",
    # Explicitly empty, so the repo's own .env cannot leak in.
    "CALDAV_URL": "",
    "CALDAV_USER": "",
    "CALDAV_PASSWORD": "",
}


@pytest.fixture
def nextcloud_env(monkeypatch):
    """A complete environment, so the repo's own .env cannot leak in."""
    for key, value in NEXTCLOUD_ENV.items():
        monkeypatch.setenv(key, value)


class TestDeriveAccount:
    def test_is_stable(self):
        assert derive_account("10312345678", salt="alice") == derive_account(
            "10312345678", salt="alice"
        )

    def test_is_salted(self):
        """An 11-digit card number alone is brute-forceable from the hash;
        the salt is what a stranger holding a UID does not have."""
        assert derive_account("10312345678", salt="alice") != derive_account(
            "10312345678", salt="bob"
        )
        assert derive_account("10312345678", salt="alice") != derive_account("10312345678")

    def test_does_not_contain_the_card_number(self):
        assert "10312345678" not in derive_account("10312345678", salt="alice")

    def test_empty_user_falls_back_to_default(self):
        assert derive_account("", salt="alice") == "default"


class TestAccountSlug:
    @pytest.mark.parametrize(
        "label,expected",
        [
            ("Sebastian", "sebastian"),
            ("Frau Müller!", "fraumller"),
            ("kids-2026", "kids2026"),
            ("***", "default"),
        ],
    )
    def test_normalises_to_uid_safe_characters(self, label, expected):
        assert account_slug(label) == expected


class TestLoadedAccount:
    def test_defaults_to_the_salted_hash(self, nextcloud_env):
        config = load_caldav_config()
        assert config.account == derive_account("10312345678", salt="alice")

    def test_different_nextcloud_users_get_different_defaults(self, nextcloud_env, monkeypatch):
        first = load_caldav_config().account
        monkeypatch.setenv("NEXTCLOUD_USER", "bob")
        assert load_caldav_config().account != first

    def test_label_beats_the_hash(self, nextcloud_env, monkeypatch):
        monkeypatch.setenv("VOEBB_ACCOUNT", "Sebastian")
        assert load_caldav_config().account == "sebastian"


class TestUrlResolution:
    """CALDAV_* is verbatim; NEXTCLOUD_* accepts a bare host."""

    def test_nextcloud_bare_host_is_completed_to_the_dav_root(self, nextcloud_env):
        assert load_caldav_config().url == "https://cloud.example.de/remote.php/dav"

    def test_nextcloud_full_dav_root_is_not_doubled(self, nextcloud_env, monkeypatch):
        monkeypatch.setenv("NEXTCLOUD_URL", "https://cloud.example.de/remote.php/dav")
        assert load_caldav_config().url == "https://cloud.example.de/remote.php/dav"

    def test_caldav_url_is_taken_verbatim(self, nextcloud_env, monkeypatch):
        """Appending Nextcloud's path to a Radicale or Baikal root would break it."""
        monkeypatch.setenv("CALDAV_URL", "https://dav.example.com/radicale/alice/")
        assert load_caldav_config().url == "https://dav.example.com/radicale/alice"

    def test_caldav_wins_where_both_are_set(self, nextcloud_env, monkeypatch):
        monkeypatch.setenv("CALDAV_URL", "https://dav.example.com/dav")
        assert load_caldav_config().url == "https://dav.example.com/dav"

    def test_caldav_credentials_fall_back_to_the_nextcloud_names(self, nextcloud_env, monkeypatch):
        monkeypatch.setenv("CALDAV_URL", "https://dav.example.com/dav")
        config = load_caldav_config()
        assert config.user == "alice"
        assert config.password == "app-pw"
