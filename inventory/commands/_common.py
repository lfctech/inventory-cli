"""
inventory.commands._common
===========================
Shared helpers for CLI subcommand modules.
"""

from __future__ import annotations

import atexit
from typing import NoReturn

import typer
from snipeit import SnipeIT
from snipeit.exceptions import (
    SnipeITAuthenticationError,
    SnipeITClientError,
    SnipeITNotFoundError,
    SnipeITServerError,
    SnipeITTimeoutError,
    SnipeITValidationError,
)

from ..client import make_client
from ..console import abort, print_error
from ..main import state


def get_client() -> SnipeIT:
    """Create a SnipeIT client, exiting with a clear error on failure.

    Pulls ``timeout`` and ``max_retries`` from the loaded config (defaults
    10 s / 3 retries). Registers ``client.close()`` with :mod:`atexit` so
    the underlying ``httpx.Client`` is torn down deterministically — this
    avoids "Unclosed client" warnings if the process exits abnormally and
    matches the recommended context-manager usage of :class:`snipeit.SnipeIT`.
    """
    cfg = state.config
    timeout = cfg.snipeit.timeout if cfg is not None else 10
    max_retries = cfg.snipeit.max_retries if cfg is not None else 3
    try:
        client = make_client(
            state.url,
            state.api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
    except ValueError as exc:
        abort(str(exc))

    atexit.register(client.close)
    return client


def handle_api_error(exc: Exception, entity: str = "Resource") -> NoReturn:
    """Print a user-friendly error and exit."""
    if isinstance(exc, SnipeITAuthenticationError):
        message = "Authentication failed. Check your API key."
    elif isinstance(exc, SnipeITNotFoundError):
        message = f"{entity} not found."
    elif isinstance(exc, SnipeITValidationError):
        message = f"Validation failed — {exc}"
    elif isinstance(exc, SnipeITServerError):
        message = "Snipe-IT server error. Try again later."
    elif isinstance(exc, SnipeITTimeoutError):
        message = "Request timed out."
    elif isinstance(exc, SnipeITClientError):
        message = f"Client error — {exc}"
    else:
        message = str(exc)

    print_error(message)
    raise typer.Exit(1)
