"""
inventory.commands._common
===========================
Shared helpers for CLI subcommand modules.
"""

from __future__ import annotations

from typing import NoReturn

import typer
from rich.console import Console
from snipeit import SnipeIT
from snipeit.exceptions import (
    SnipeITAuthenticationError,
    SnipeITNotFoundError,
    SnipeITServerError,
    SnipeITTimeoutError,
    SnipeITValidationError,
)

from ..client import make_client
from ..main import state

console = Console(stderr=True)


def get_client() -> SnipeIT:
    """Create a SnipeIT client, exiting with a clear error on failure."""
    try:
        return make_client(state.url, state.api_key)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None


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
    else:
        console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(1)
