"""
inventory.commands.assets
=========================
``inventory assets`` subcommand group: create, get, update, price, label.
"""

from __future__ import annotations

from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.table import Table
from snipeit import SnipeIT
from snipeit.exceptions import (
    SnipeITAuthenticationError,
    SnipeITNotFoundError,
    SnipeITServerError,
    SnipeITTimeoutError,
    SnipeITValidationError,
)

from ..client import make_client
from ..config import AppConfig
from ..core.passmark import get_average_score, lookup_csv
from ..core.pricing import calculate_price, is_desktop_category
from ..core.resolvers import resolve_model, resolve_status_label
from ..main import state

console = Console(stderr=True)
out = Console()  # stdout for data output

assets_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_config() -> AppConfig:
    """Ensure config is loaded, exit with a helpful message if not."""
    cfg = state.config
    if cfg is None:
        console.print(
            "[red]Error:[/red] No config.toml found.\n"
            "  Pass --config <path>, set INVENTORY_CONFIG, or run [bold]inventory init[/bold]."
        )
        raise typer.Exit(1)
    return cfg


def _get_client() -> SnipeIT:
    """Create a SnipeIT client, exiting with a clear error on failure."""
    try:
        return make_client(state.url, state.api_key)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None


def _handle_api_error(exc: Exception) -> NoReturn:
    """Print a user-friendly error and exit."""
    if isinstance(exc, SnipeITAuthenticationError):
        console.print("[red]Error:[/red] Authentication failed. Check your API key.")
    elif isinstance(exc, SnipeITNotFoundError):
        console.print("[red]Error:[/red] Asset not found.")
    elif isinstance(exc, SnipeITValidationError):
        console.print(f"[red]Error:[/red] Validation failed — {exc}")
    elif isinstance(exc, SnipeITServerError):
        console.print("[red]Error:[/red] Snipe-IT server error. Try again later.")
    elif isinstance(exc, SnipeITTimeoutError):
        console.print("[red]Error:[/red] Request timed out.")
    else:
        console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(1)


def _resolve_asset(
    client: SnipeIT,
    asset_id: int | None,
    tag: str | None,
    serial: str | None,
) -> Any:
    """Resolve a single asset by ID, tag, or serial. Exits on failure."""
    count = sum(1 for v in (asset_id, tag, serial) if v is not None)
    if count == 0:
        console.print("[red]Error:[/red] Provide one of --id, --tag, or --serial.")
        raise typer.Exit(1)
    if count > 1:
        console.print("[red]Error:[/red] Provide only one of --id, --tag, or --serial.")
        raise typer.Exit(1)

    try:
        if asset_id is not None:
            return client.assets.get(asset_id)
        elif tag is not None:
            return client.assets.get_by_tag(tag)
        else:
            assert serial is not None
            return client.assets.get_by_serial(serial)
    except Exception as exc:
        _handle_api_error(exc)


def _get_custom_field_value(asset: Any, field_key: str) -> str | None:
    """Safely read a custom field value from an asset object."""
    raw = getattr(asset, "custom_fields", None)
    if not raw:
        return None
    if isinstance(raw, dict):
        entry = raw.get(field_key)
        if isinstance(entry, dict):
            val = entry.get("value")
        else:
            val = entry
    else:
        val = getattr(raw, field_key, None)
    if val is None or str(val).strip() in ("", "None"):
        return None
    return str(val).strip()


def _asset_table(asset: Any, cfg: AppConfig) -> Table:
    """Build a rich Table from an asset object and config."""
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("ID", str(getattr(asset, "id", "")))
    table.add_row("Asset Tag", str(getattr(asset, "asset_tag", "")))
    table.add_row("Name", str(getattr(asset, "name", "") or ""))
    table.add_row("Serial", str(getattr(asset, "serial", "") or ""))

    model = getattr(asset, "model", None)
    model_name = model.get("name", "") if isinstance(model, dict) else str(model or "")
    table.add_row("Model", model_name)

    status = getattr(asset, "status_label", None)
    status_name = status.get("name", "") if isinstance(status, dict) else str(status or "")
    table.add_row("Status", status_name)

    category = getattr(asset, "category", None)
    cat_name = category.get("name", "") if isinstance(category, dict) else str(category or "")
    table.add_row("Category", cat_name)

    # Custom fields
    cpu = _get_custom_field_value(asset, cfg.custom_fields.cpu_model) or ""
    table.add_row("CPU", cpu)

    passmark = _get_custom_field_value(asset, cfg.custom_fields.cpu_passmark) or ""
    table.add_row("PassMark", passmark)

    ram = _get_custom_field_value(asset, cfg.custom_fields.ram_gb) or ""
    table.add_row("RAM", f"{ram} GB" if ram else "")

    storage = _get_custom_field_value(asset, cfg.custom_fields.storage_gb) or ""
    table.add_row("Storage", f"{storage} GB" if storage else "")

    touch = _get_custom_field_value(asset, cfg.custom_fields.touch_screen) or ""
    table.add_row("Touch Screen", touch)

    price = _get_custom_field_value(asset, cfg.custom_fields.sale_price) or ""
    table.add_row("Sale Price", f"${price}" if price else "")

    return table


def _asset_dict(asset: Any, cfg: AppConfig) -> dict:
    """Build a JSON-serializable dict from an asset object."""
    model = getattr(asset, "model", None)
    status = getattr(asset, "status_label", None)
    category = getattr(asset, "category", None)
    return {
        "id": getattr(asset, "id", None),
        "asset_tag": getattr(asset, "asset_tag", None),
        "name": getattr(asset, "name", None),
        "serial": getattr(asset, "serial", None),
        "model": model.get("name") if isinstance(model, dict) else str(model or ""),
        "status": status.get("name") if isinstance(status, dict) else str(status or ""),
        "category": category.get("name") if isinstance(category, dict) else str(category or ""),
        "cpu": _get_custom_field_value(asset, cfg.custom_fields.cpu_model),
        "passmark": _get_custom_field_value(asset, cfg.custom_fields.cpu_passmark),
        "ram_gb": _get_custom_field_value(asset, cfg.custom_fields.ram_gb),
        "storage_gb": _get_custom_field_value(asset, cfg.custom_fields.storage_gb),
        "touch_screen": _get_custom_field_value(asset, cfg.custom_fields.touch_screen),
        "sale_price": _get_custom_field_value(asset, cfg.custom_fields.sale_price),
    }


def _build_custom_fields(
    cfg: AppConfig,
    cpu: str | None = None,
    ram: int | None = None,
    storage: int | None = None,
    touch_screen: bool | None = None,
    passmark: int | None = None,
    sale_price: float | None = None,
) -> dict[str, str]:
    """Map optional hardware values to their Snipe-IT custom field keys."""
    fields: dict[str, str] = {}
    if cpu is not None:
        fields[cfg.custom_fields.cpu_model] = cpu
    if ram is not None:
        fields[cfg.custom_fields.ram_gb] = str(ram)
    if storage is not None:
        fields[cfg.custom_fields.storage_gb] = str(storage)
    if touch_screen is not None:
        fields[cfg.custom_fields.touch_screen] = "1" if touch_screen else "0"
    if passmark is not None:
        fields[cfg.custom_fields.cpu_passmark] = str(passmark)
    if sale_price is not None:
        fields[cfg.custom_fields.sale_price] = str(int(sale_price))
    return fields


# ── Commands ──────────────────────────────────────────────────────────────────

@assets_app.command()
def get(
    id: int | None = typer.Option(None, "--id", help="Asset ID."),
    tag: str | None = typer.Option(None, "--tag", help="Asset tag."),
    serial: str | None = typer.Option(None, "--serial", help="Serial number."),
) -> None:
    """Fetch and display a single asset."""
    cfg = _require_config()
    client = _get_client()

    asset = _resolve_asset(client, id, tag, serial)

    if state.json_output:
        out.print_json(data=_asset_dict(asset, cfg))
    else:
        out.print(_asset_table(asset, cfg))


@assets_app.command()
def create(
    model: str = typer.Option(..., "--model", help="Model name (resolved to ID via API)."),
    status: str = typer.Option(..., "--status", help="Status label name (resolved to ID via API)."),
    asset_tag: str | None = typer.Option(None, "--asset-tag", help="Asset tag (auto-assigned if omitted)."),
    serial: str | None = typer.Option(None, "--serial", help="Serial number."),
    name: str | None = typer.Option(None, "--name", help="Asset name."),
    cpu: str | None = typer.Option(None, "--cpu", help="CPU model string."),
    ram: int | None = typer.Option(None, "--ram", help="RAM in GB."),
    storage: int | None = typer.Option(None, "--storage", help="Storage in GB."),
    touch_screen: bool | None = typer.Option(None, "--touch-screen/--no-touch-screen", help="Has touch screen."),
    passmark: int | None = typer.Option(None, "--passmark", help="CPU PassMark score."),
) -> None:
    """Create a new asset in Snipe-IT."""
    cfg = _require_config()
    client = _get_client()

    # Resolve model and status names to IDs
    try:
        model_id = resolve_model(client, model)
        status_id = resolve_status_label(client, status)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None

    # Build payload
    payload: dict[str, Any] = {
        "status_id": status_id,
        "model_id": model_id,
    }
    if asset_tag is not None:
        payload["asset_tag"] = asset_tag
    if serial is not None:
        payload["serial"] = serial
    if name is not None:
        payload["name"] = name

    # Custom fields
    payload.update(_build_custom_fields(cfg, cpu=cpu, ram=ram, storage=storage, touch_screen=touch_screen, passmark=passmark))

    try:
        asset = client.assets.create(**payload)
    except Exception as exc:
        _handle_api_error(exc)

    if state.json_output:
        out.print_json(data=_asset_dict(asset, cfg))
    else:
        console.print("[green]✓[/green] Asset created.")
        out.print(_asset_table(asset, cfg))


@assets_app.command()
def update(
    id: int | None = typer.Option(None, "--id", help="Asset ID (to look up)."),
    tag: str | None = typer.Option(None, "--tag", help="Asset tag (to look up)."),
    serial: str | None = typer.Option(None, "--serial", help="Serial number (to look up)."),
    model: str | None = typer.Option(None, "--model", help="Model name (resolved to ID via API)."),
    status: str | None = typer.Option(None, "--status", help="Status label name (resolved to ID via API)."),
    name: str | None = typer.Option(None, "--name", help="Asset name."),
    cpu: str | None = typer.Option(None, "--cpu", help="CPU model string."),
    ram: int | None = typer.Option(None, "--ram", help="RAM in GB."),
    storage: int | None = typer.Option(None, "--storage", help="Storage in GB."),
    touch_screen: bool | None = typer.Option(None, "--touch-screen/--no-touch-screen", help="Has touch screen."),
    passmark: int | None = typer.Option(None, "--passmark", help="CPU PassMark score."),
    sale_price: float | None = typer.Option(None, "--sale-price", help="Sale price in dollars."),
) -> None:
    """Update one or more fields on an existing asset."""
    cfg = _require_config()
    client = _get_client()

    asset = _resolve_asset(client, id, tag, serial)
    asset_id = asset.id

    # Build partial payload from provided options
    payload: dict[str, Any] = {}

    if model is not None:
        try:
            payload["model_id"] = resolve_model(client, model)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from None

    if status is not None:
        try:
            payload["status_id"] = resolve_status_label(client, status)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from None

    if name is not None:
        payload["name"] = name
    payload.update(_build_custom_fields(
        cfg, cpu=cpu, ram=ram, storage=storage,
        touch_screen=touch_screen, passmark=passmark, sale_price=sale_price,
    ))

    if not payload:
        console.print("[yellow]Warning:[/yellow] No fields to update.")
        raise typer.Exit(0)

    try:
        updated = client.assets.patch(asset_id, **payload)
    except Exception as exc:
        _handle_api_error(exc)

    if state.json_output:
        out.print_json(data=_asset_dict(updated, cfg))
    else:
        console.print(f"[green]✓[/green] Asset {asset_id} updated.")
        out.print(_asset_table(updated, cfg))


@assets_app.command()
def price(
    id: int | None = typer.Option(None, "--id", help="Asset ID."),
    tag: str | None = typer.Option(None, "--tag", help="Asset tag."),
    serial: str | None = typer.Option(None, "--serial", help="Serial number."),
    passmark_override: int | None = typer.Option(None, "--passmark", help="Override PassMark score."),
    ram_override: int | None = typer.Option(None, "--ram", help="RAM in GB (reads from asset if omitted)."),
    storage_override: int | None = typer.Option(None, "--storage", help="Storage in GB (reads from asset if omitted)."),
    touch_override: bool | None = typer.Option(None, "--touch-screen/--no-touch-screen", help="Has touch screen (reads from asset if omitted)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print price without writing to Snipe-IT."),
) -> None:
    """Calculate the sale price and write it to the asset."""
    cfg = _require_config()
    client = _get_client()

    asset = _resolve_asset(client, id, tag, serial)
    asset_id = asset.id
    asset_tag = getattr(asset, "asset_tag", "?")

    # ── Resolve specs from asset or overrides ─────────────────────────────
    # RAM
    if ram_override is not None:
        ram_gb = float(ram_override)
    else:
        raw = _get_custom_field_value(asset, cfg.custom_fields.ram_gb)
        if raw:
            try:
                ram_gb = float(raw)
            except ValueError:
                console.print(f"[red]Error:[/red] Cannot parse RAM value '{raw}' from asset. Pass --ram.")
                raise typer.Exit(1) from None
        else:
            console.print("[red]Error:[/red] Asset has no RAM value. Pass --ram.")
            raise typer.Exit(1)

    # Storage
    if storage_override is not None:
        storage_gb = float(storage_override)
    else:
        raw = _get_custom_field_value(asset, cfg.custom_fields.storage_gb)
        if raw:
            try:
                storage_gb = float(raw)
            except ValueError:
                console.print(f"[red]Error:[/red] Cannot parse storage value '{raw}' from asset. Pass --storage.")
                raise typer.Exit(1) from None
        else:
            console.print("[red]Error:[/red] Asset has no storage value. Pass --storage.")
            raise typer.Exit(1)

    # Touch screen
    if touch_override is not None:
        has_touch = touch_override
    else:
        raw = _get_custom_field_value(asset, cfg.custom_fields.touch_screen)
        has_touch = raw is not None and raw.lower() in ("1", "true", "yes")

    # Chassis from category.name
    category = getattr(asset, "category", None)
    cat_name = category.get("name", "") if isinstance(category, dict) else str(category or "")
    is_desktop = is_desktop_category(cat_name)

    # ── Resolve PassMark score ────────────────────────────────────────────
    passmark_score: int | None = None
    passmark_source = "unknown"

    # 1. CLI override
    if passmark_override is not None and passmark_override > 0:
        passmark_score = passmark_override
        passmark_source = "override"

    # 2. Existing value in Snipe-IT
    if passmark_score is None:
        raw = _get_custom_field_value(asset, cfg.custom_fields.cpu_passmark)
        if raw:
            try:
                passmark_score = int(float(raw))
                passmark_source = "inventory"
            except ValueError:
                pass

    # 3. CSV fuzzy match
    if passmark_score is None:
        cpu_name = _get_custom_field_value(asset, cfg.custom_fields.cpu_model)
        if cpu_name:
            console.print(f"Looking up CPU '[bold]{cpu_name}[/bold]' in PassMark database...")
            result = lookup_csv(cpu_name)
            if result:
                console.print(
                    f"  Best match: [cyan]{result['matched_cpu']}[/cyan]  "
                    f"score: [bold]{result['score']}[/bold]  "
                    f"confidence: {result['confidence']}%"
                )
                if result["confidence"] >= cfg.passmark.fuzzy_threshold:
                    passmark_score = result["score"]
                    passmark_source = "csv_auto"
                else:
                    # Prompt operator
                    console.print(
                        f"  [yellow]Low confidence ({result['confidence']}% < {cfg.passmark.fuzzy_threshold}%)[/yellow]"
                    )
                    accept = typer.confirm("  Accept this match?", default=False)
                    if accept:
                        passmark_score = result["score"]
                        passmark_source = "csv_confirmed"

    # 4. Average fallback
    if passmark_score is None:
        passmark_score = get_average_score()
        passmark_source = "csv_average"
        cpu_name = _get_custom_field_value(asset, cfg.custom_fields.cpu_model) or "unknown"
        console.print(
            f"[yellow]Warning:[/yellow] Using CSV average ({passmark_score}) for '{cpu_name}'. "
            "Price may not reflect actual performance."
        )

    # ── Calculate ─────────────────────────────────────────────────────────
    breakdown = calculate_price(
        passmark_score=passmark_score,
        ram_gb=ram_gb,
        storage_gb=storage_gb,
        config=cfg.pricing,
        is_desktop=is_desktop,
        has_touch=has_touch,
    )

    # ── Output ────────────────────────────────────────────────────────────
    if state.json_output:
        data = {
            "asset_id": asset_id,
            "asset_tag": asset_tag,
            "passmark_score": passmark_score,
            "passmark_source": passmark_source,
            **breakdown.as_dict(),
        }
        out.print_json(data=data)
    else:
        table = Table(title="Price Calculation", show_header=True, header_style="bold cyan")
        table.add_column("Component", style="bold")
        table.add_column("Value", justify="right")
        table.add_column("Points", justify="right")

        table.add_row("CPU PassMark", str(breakdown.passmark_score), str(breakdown.cpu_points))
        table.add_row("RAM", f"{breakdown.ram_gb} GB", str(breakdown.ram_points))
        table.add_row("Storage", f"{breakdown.storage_gb} GB", str(breakdown.storage_points))
        if breakdown.is_desktop:
            table.add_row("Desktop Penalty", "", str(breakdown.desktop_adjustment))
        table.add_row("", "", "───")
        table.add_row("[bold]Total Points[/bold]", "", f"[bold]{breakdown.total_points}[/bold]")
        table.add_row("[bold]Base Price[/bold]", f"[bold]${breakdown.base_price}[/bold]", "")
        if breakdown.has_touch:
            table.add_row("Touch Bonus", f"+${breakdown.touch_bonus}", "")
        table.add_row("[bold green]Final Price[/bold green]", f"[bold green]${breakdown.final_price}[/bold green]", "")
        table.add_row("", "", "")
        table.add_row("PassMark Source", passmark_source, "")

        out.print(table)

    # ── Write to Snipe-IT ─────────────────────────────────────────────────
    if dry_run:
        console.print("[yellow]Dry run — no changes written to Snipe-IT.[/yellow]")
        return

    try:
        fields = {
            cfg.custom_fields.cpu_passmark: str(passmark_score),
            cfg.custom_fields.sale_price: str(breakdown.final_price),
        }
        client.assets.patch(asset_id, **fields)
        console.print(
            f"[green]✓[/green] Asset {asset_tag} updated — sale price: [bold]${breakdown.final_price}[/bold]"
        )
    except Exception as exc:
        _handle_api_error(exc)


@assets_app.command()
def label(
    id: int | None = typer.Option(None, "--id", help="Asset ID."),
    tag: str | None = typer.Option(None, "--tag", help="Asset tag."),
    serial: str | None = typer.Option(None, "--serial", help="Serial number."),
    output: str = typer.Option("./label.pdf", "--output", "-o", help="Where to save the PDF."),
) -> None:
    """Generate and save the label PDF for an asset."""
    _require_config()
    client = _get_client()

    asset = _resolve_asset(client, id, tag, serial)
    asset_tag = getattr(asset, "asset_tag", None)

    if not asset_tag:
        console.print("[red]Error:[/red] Asset has no asset tag — cannot generate label.")
        raise typer.Exit(1)

    try:
        save_path = client.assets.labels(output, [asset_tag])
        console.print(f"[green]✓[/green] Label saved to: {save_path}")
    except Exception as exc:
        _handle_api_error(exc)
