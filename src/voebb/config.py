"""Credential loading. Values are read from the environment and never logged."""

from __future__ import annotations

import os
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
