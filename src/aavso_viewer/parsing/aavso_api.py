"""Shared request settings for AAVSO's online services.

As of 2026-09 www.aavso.org sits behind a Cloudflare challenge that blocks scripted
requests outright; the same services are reachable on apps.aavso.org instead. AAVSO's
API docs (https://docs.aavso.org/overview-1.md) ask for an `Authorization: Token <key>`
header -- the endpoints this app uses currently answer without one, but it's sent
whenever a key is configured so a future tightening doesn't silently break lookups.

The key is read from the AAVSO_API_KEY environment variable, falling back to an
AAVSO_API_KEY=... line in the project root's .env file (gitignored -- this repo is
public, so the key must never live in source).
"""

from __future__ import annotations

import os
from pathlib import Path

APPS_BASE_URL = "https://apps.aavso.org"

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def _read_env_file_key() -> str | None:
    try:
        lines = _ENV_FILE.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip() == "AAVSO_API_KEY":
            return value.strip().strip("'\"") or None
    return None


def api_key() -> str | None:
    return os.environ.get("AAVSO_API_KEY") or _read_env_file_key()


def auth_headers() -> dict[str, str]:
    key = api_key()
    return {"Authorization": f"Token {key}"} if key else {}
