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

    URL resolution order:
      1. Explicit ``url`` argument (from --url CLI flag)
      2. SNIPERIT_URL environment variable
      3. config.toml [snipeit].url (caller resolves and passes as ``url``)

    API key resolution order:
      1. Explicit ``api_key`` argument (from --api-key CLI flag)
      2. SNIPERIT_API_KEY environment variable

    Raises ValueError if required values cannot be resolved.
    """
    resolved_url = url or os.environ.get("SNIPERIT_URL")
    resolved_key = api_key or os.environ.get("SNIPERIT_API_KEY")

    if not resolved_url:
        raise ValueError(
            "Snipe-IT URL not set. Use config.toml [snipeit].url, SNIPERIT_URL env var, or --url."
        )
    if not resolved_key:
        raise ValueError(
            "API key not set. Set SNIPERIT_API_KEY or pass --api-key."
        )

    return SnipeIT(url=resolved_url, token=resolved_key)
