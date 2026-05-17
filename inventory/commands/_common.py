"""
inventory.commands._common
===========================
Shared helpers for CLI subcommand modules.
"""

from __future__ import annotations

import atexit
from typing import NoReturn

import typer
from rich.console import Console
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
from ..main import state

console = Console(stderr=True)


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
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None

    atexit.register(client.close)
    return client


def handle_api_error(exc: Exception, entity: str = "Resource") -> NoReturn:
    """Print a user-friendly error and exit."""
    if isinstance(exc, SnipeITAuthenticationError):
        console.print("[red]Error:[/red] Authentication failed. Check your API key.")
    elif isinstance(exc, SnipeITNotFoundError):
        console.print(f"[red]Error:[/red] {entity} not found.")
    elif isinstance(exc, SnipeITValidationError):
        console.print(f"[red]Error:[/red] Validation failed — {exc}")
    elif isinstance(exc, SnipeITServerError):
        console.print("[red]Error:[/red] Snipe-IT server error. Try again later.")
    elif isinstance(exc, SnipeITTimeoutError):
        console.print("[red]Error:[/red] Request timed out.")
    elif isinstance(exc, SnipeITClientError):
        console.print(f"[red]Error:[/red] Client error — {exc}")
    else:
        console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(1)
