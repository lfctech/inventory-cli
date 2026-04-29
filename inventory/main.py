"""
inventory.main
==============
Typer CLI entry point.  Defines the root ``inventory`` app, global options
(--url, --api-key, --config), and mounts subcommand groups.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

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


state = _State()


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
) -> None:
    """Snipe-IT inventory management CLI."""
    state.url = url
    state.api_key = api_key
    state.json_output = json_output

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


# ── Mount subcommand groups ──────────────────────────────────────────────────

from .commands.assets import assets_app  # noqa: E402
from .commands.models import models_app  # noqa: E402

app.add_typer(assets_app, name="assets", help="Manage Snipe-IT assets.")
app.add_typer(models_app, name="models", help="Manage Snipe-IT models.")
