"""
inventory.main
==============
Typer CLI entry point.  Defines the root ``inventory`` app, global options
(--url, --api-key, --config, --version, --verbose), and mounts subcommand
groups.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console

from . import __version__
from .config import (
    DEFAULT_CONFIG_TEMPLATE,
    AppConfig,
    _xdg_config_path,
    load_config,
    resolve_config_path,
)

console = Console(stderr=True)

app = typer.Typer(
    name="inventory",
    help="Snipe-IT inventory management CLI.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ── Shared state ──────────────────────────────────────────────────────────────
# These are set by the root callback and consumed by subcommands.

class _State:
    url: str | None = None
    api_key: str | None = None
    config: AppConfig | None = None
    json_output: bool = False
    verbose: int = 0


state = _State()


# ── Eager --version callback ─────────────────────────────────────────────────


def _version_callback(value: bool) -> None:
    """Print the package version and exit (eager: runs before all other options)."""
    if value:
        typer.echo(f"inventory {__version__}")
        raise typer.Exit()


# ── Logging setup ────────────────────────────────────────────────────────────


def _configure_logging(verbose: int) -> None:
    """Wire up the snipeit / snipeit.http loggers based on --verbose count.

    -v     Enable INFO/WARNING from the ``snipeit`` logger (retries, timeouts).
    -vv    Also enable DEBUG from ``snipeit.http`` (per-request traces).

    The library is careful to never log API tokens or Authorization headers.
    """
    if verbose <= 0:
        return

    # Configure a stderr handler once so messages are visible without the
    # caller having to do logging.basicConfig() themselves.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    snipeit_logger = logging.getLogger("snipeit")
    snipeit_logger.addHandler(handler)
    snipeit_logger.setLevel(logging.DEBUG if verbose >= 1 else logging.WARNING)

    if verbose >= 2:
        http_logger = logging.getLogger("snipeit.http")
        http_logger.addHandler(handler)
        http_logger.setLevel(logging.DEBUG)


# ── Root callback (global options) ────────────────────────────────────────────

@app.callback()
def main(
    url: str | None = typer.Option(
        None,
        "--url",
        envvar="SNIPEIT_URL",
        help="Snipe-IT instance URL.",
        show_default=False,
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="SNIPEIT_API_KEY",
        help="Snipe-IT API key.",
        show_default=False,
    ),
    config_path: str | None = typer.Option(
        None,
        "--config",
        envvar="INVENTORY_CONFIG",
        help="Path to config.toml.",
        show_default=False,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output in JSON format.",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase log verbosity. -v: snipeit warnings/info; -vv: include HTTP trace.",
    ),
    _version: bool | None = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Snipe-IT inventory management CLI."""
    state.url = url
    state.api_key = api_key
    state.json_output = json_output
    state.verbose = verbose

    _configure_logging(verbose)

    # Resolve and load config
    try:
        resolved = resolve_config_path(config_path)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None
    if resolved is None:
        # The `init` command doesn't need config — skip loading
        # We check for it inside commands that need it
        return
    try:
        state.config = load_config(resolved)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None

    # Apply config URL as fallback (flag/env take priority — already captured above)
    if state.url is None and state.config.snipeit.url:
        state.url = state.config.snipeit.url


# ── Init command ──────────────────────────────────────────────────────────────

@app.command()
def init(
    output: str | None = typer.Option(
        None,
        "--output", "-o",
        help="Where to write config.toml. Defaults to ~/.config/inventory/config.toml.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing config.toml.",
    ),
) -> None:
    """Write a starter config.toml to get started quickly."""
    target = Path(output) if output else _xdg_config_path()
    target = target.expanduser().resolve()

    if target.exists() and not force:
        console.print(
            f"[yellow]Config already exists at:[/yellow] {target}\n"
            f"Use [bold]--force[/bold] to overwrite."
        )
        raise typer.Exit(1)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DEFAULT_CONFIG_TEMPLATE)
    console.print(f"[green]✓[/green] Config written to: {target}")
    console.print("  Edit the [bold][custom_fields][/bold] section to match your Snipe-IT instance.")


# ── Version command ──────────────────────────────────────────────────────────


@app.command()
def version() -> None:
    """Show the inventory CLI version."""
    typer.echo(f"inventory {__version__}")


# ── Mount subcommand groups ──────────────────────────────────────────────────

from .commands.assets import assets_app  # noqa: E402
from .commands.models import models_app  # noqa: E402

app.add_typer(assets_app, name="assets", help="Manage Snipe-IT assets.")
app.add_typer(models_app, name="models", help="Manage Snipe-IT models.")
