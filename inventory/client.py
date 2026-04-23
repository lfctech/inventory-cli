"""
inventory.client
================
SnipeIT client factory.  No .env loading — secrets come from the environment
or explicit CLI flags.
"""

from __future__ import annotations

import os

from snipeit import SnipeIT


def make_client(url: str | None = None, api_key: str | None = None) -> SnipeIT:
    """
    Create a configured SnipeIT client.

    Resolution order:
      1. Explicit arguments (from --url / --api-key CLI flags)
      2. Environment variables (SNIPERIT_URL / SNIPERIT_API_KEY)

    Raises ValueError if neither source provides the required values.
    """
    resolved_url = url or os.environ.get("SNIPERIT_URL")
    resolved_key = api_key or os.environ.get("SNIPERIT_API_KEY")

    if not resolved_url:
        raise ValueError(
            "Snipe-IT URL not set. Set SNIPERIT_URL or pass --url."
        )
    if not resolved_key:
        raise ValueError(
            "API key not set. Set SNIPERIT_API_KEY or pass --api-key."
        )

    return SnipeIT(url=resolved_url, token=resolved_key)
