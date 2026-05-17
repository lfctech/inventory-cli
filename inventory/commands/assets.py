"""
inventory.commands.assets
=========================
``inventory assets`` subcommand group: get, create, update, price, label,
plus a ``files`` subgroup (list/upload/download/delete).
"""

from __future__ import annotations

import os
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from snipeit import SnipeIT
from snipeit.exceptions import SnipeITException
from snipeit.resources.assets import Asset

from ..config import AppConfig
from ..core.passmark import get_average_score, lookup_csv
from ..core.pricing import calculate_price, is_desktop_category
from ..core.resolvers import resolve_model, resolve_status_label
from ..main import state
from ._common import get_client, handle_api_error
from ._lookup import AssetID, AssetSerial, AssetTag

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


def _resolve_asset(
    client: SnipeIT,
    asset_id: int | None,
    tag: str | None,
    serial: str | None,
) -> Asset:
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
    except SnipeITException as exc:
        handle_api_error(exc, entity="Asset")


def _require_int_id(asset: Asset) -> int:
    """Return ``asset.id`` as an ``int``, exiting with a clear error otherwise.

    ``Asset.id`` is typed ``int | str | None`` because Snipe-IT can return
    string IDs in some response shapes. The CLI's downstream API calls
    (``list_files``, ``upload_files``, etc.) all require ``int``, so this
    helper centralises the narrowing and the failure message.
    """
    aid = asset.id
    if aid is None:
        console.print("[red]Error:[/red] Asset has no ID.")
        raise typer.Exit(1)
    try:
        return int(aid)
    except (TypeError, ValueError):
        console.print(f"[red]Error:[/red] Asset has non-integer ID: {aid!r}.")
        raise typer.Exit(1) from None


def _get_custom_field_value(asset: Asset, label: str) -> str | None:
    """Read a custom field value from an asset by display label."""
    val = asset.get_custom_field(label)
    if val is None or str(val).strip() in ("", "None"):
        return None
    return str(val).strip()


def _asset_table(asset: Asset, cfg: AppConfig) -> Table:
    """Build a rich Table from an asset object and config."""
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("ID", str(asset.id or ""))
    table.add_row("Asset Tag", str(asset.asset_tag or ""))
    table.add_row("Name", str(asset.name or ""))
    table.add_row("Serial", str(asset.serial or ""))

    model = asset.model
    model_name = model.get("name", "") if isinstance(model, dict) else ""
    table.add_row("Model", model_name)

    status = getattr(asset, "status_label", None)
    status_name = status.get("name", "") if isinstance(status, dict) else ""
    table.add_row("Status", status_name)

    category = getattr(asset, "category", None)
    cat_name = category.get("name", "") if isinstance(category, dict) else ""
    table.add_row("Category", cat_name)

    # Custom fields
    cpu = _get_custom_field_value(asset, cfg.custom_fields.cpu_model) or ""
    table.add_row("CPU", cpu)

    passmark = _get_custom_field_value(asset, cfg.custom_fields.cpu_passmark) or ""
    table.add_row("PassMark", passmark)

    ram = _get_custom_field_value(asset, cfg.custom_fields.ram) or ""
    table.add_row("RAM", f"{ram} GB" if ram else "")

    storage = _get_custom_field_value(asset, cfg.custom_fields.storage) or ""
    table.add_row("Storage", f"{storage} GB" if storage else "")

    touch_raw = _get_custom_field_value(asset, cfg.custom_fields.touch_screen) or ""
    if touch_raw == "1":
        touch = "[green]✓[/green]"
    elif touch_raw == "0":
        touch = "[red]✗[/red]"
    elif touch_raw.lower() in ("true", "yes"):
        touch = "[green]✓[/green]"
    elif touch_raw.lower() in ("false", "no"):
        touch = "[red]✗[/red]"
    else:
        touch = touch_raw
    table.add_row("Touch Screen", touch)

    price = _get_custom_field_value(asset, cfg.custom_fields.sale_price) or ""
    table.add_row("Sale Price", f"${price}" if price else "")

    return table


def _asset_dict(asset: Asset, cfg: AppConfig) -> dict:
    """Build a JSON-serializable dict from an asset object."""
    model = asset.model
    status = getattr(asset, "status_label", None)
    category = getattr(asset, "category", None)
    return {
        "id": asset.id,
        "asset_tag": asset.asset_tag,
        "name": asset.name,
        "serial": asset.serial,
        "model": model.get("name") if isinstance(model, dict) else "",
        "status": status.get("name") if isinstance(status, dict) else "",
        "category": category.get("name") if isinstance(category, dict) else "",
        "cpu": _get_custom_field_value(asset, cfg.custom_fields.cpu_model),
        "passmark": _get_custom_field_value(asset, cfg.custom_fields.cpu_passmark),
        "ram": _get_custom_field_value(asset, cfg.custom_fields.ram),
        "storage": _get_custom_field_value(asset, cfg.custom_fields.storage),
        "touch_screen": _get_custom_field_value(asset, cfg.custom_fields.touch_screen),
        "sale_price": _get_custom_field_value(asset, cfg.custom_fields.sale_price),
    }


def _set_custom_fields(
    asset: Asset,
    cfg: AppConfig,
    cpu: str | None = None,
    ram: int | None = None,
    storage: int | None = None,
    touch_screen: bool | None = None,
    passmark: int | None = None,
    sale_price: float | None = None,
) -> None:
    """Stage custom field values on an asset for the next save()."""
    if cpu is not None:
        asset.set_custom_field(cfg.custom_fields.cpu_model, cpu)
    if ram is not None:
        asset.set_custom_field(cfg.custom_fields.ram, str(ram))
    if storage is not None:
        asset.set_custom_field(cfg.custom_fields.storage, str(storage))
    if touch_screen is not None:
        asset.set_custom_field(cfg.custom_fields.touch_screen, "1" if touch_screen else "0")
    if passmark is not None:
        asset.set_custom_field(cfg.custom_fields.cpu_passmark, str(passmark))
    if sale_price is not None:
        asset.set_custom_field(cfg.custom_fields.sale_price, str(int(sale_price)))


# ── Commands ──────────────────────────────────────────────────────────────────

@assets_app.command()
def get(
    id: AssetID = None,
    tag: AssetTag = None,
    serial: AssetSerial = None,
) -> None:
    """Fetch and display a single asset."""
    cfg = _require_config()
    client = get_client()

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
    client = get_client()

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

    # Step 1: create the asset.
    try:
        asset = client.assets.create(**payload)
    except SnipeITException as exc:
        handle_api_error(exc, entity="Asset")

    if asset.id is None:
        console.print("[red]Error:[/red] Asset was created but the server returned no ID.")
        raise typer.Exit(1)

    # Step 2: refresh + apply custom fields. If anything goes wrong here the
    # asset already exists in Snipe-IT, so report partial success and tell the
    # user how to recover rather than crashing with a bare stack trace.
    has_custom_fields = any(
        v is not None for v in (cpu, ram, storage, touch_screen, passmark)
    )
    if has_custom_fields:
        try:
            asset.refresh()
            _set_custom_fields(
                asset=asset,
                cfg=cfg,
                cpu=cpu,
                ram=ram,
                storage=storage,
                touch_screen=touch_screen,
                passmark=passmark,
            )
            if asset.pending_custom_fields():
                asset.save()
        except (SnipeITException, RuntimeError) as exc:
            console.print(
                f"[yellow]Warning:[/yellow] Asset {asset.id} was created, but custom "
                f"fields could not be applied: {exc}\n"
                f"  Re-run with [bold]inventory assets update --id {asset.id}[/bold] "
                "to retry."
            )
            raise typer.Exit(1) from None

    if state.json_output:
        out.print_json(data=_asset_dict(asset, cfg))
    else:
        console.print("[green]✓[/green] Asset created.")
        out.print(_asset_table(asset, cfg))


@assets_app.command()
def update(
    id: AssetID = None,
    tag: AssetTag = None,
    serial: AssetSerial = None,
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
    client = get_client()

    asset = _resolve_asset(client, id, tag, serial)

    # Set regular fields via active-record pattern
    has_changes = False

    if model is not None:
        try:
            asset.model_id = resolve_model(client, model)
            has_changes = True
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from None

    if status is not None:
        try:
            asset.status_id = resolve_status_label(client, status)
            has_changes = True
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from None

    if name is not None:
        asset.name = name
        has_changes = True

    # Set custom fields
    _set_custom_fields(
        asset=asset,
        cfg=cfg,
        cpu=cpu,
        ram=ram,
        storage=storage,
        touch_screen=touch_screen,
        passmark=passmark,
        sale_price=sale_price,
    )
    if asset.pending_custom_fields():
        has_changes = True

    if not has_changes:
        console.print("[yellow]Warning:[/yellow] No fields to update.")
        raise typer.Exit(0)

    try:
        asset.save()
    except (SnipeITException, RuntimeError) as exc:
        handle_api_error(exc, entity="Asset")

    if state.json_output:
        out.print_json(data=_asset_dict(asset, cfg))
    else:
        console.print(f"[green]✓[/green] Asset {asset.id} updated.")
        out.print(_asset_table(asset, cfg))


@assets_app.command()
def price(
    id: AssetID = None,
    tag: AssetTag = None,
    serial: AssetSerial = None,
    passmark_override: int | None = typer.Option(None, "--passmark", help="Override PassMark score."),
    ram_override: int | None = typer.Option(None, "--ram", help="RAM in GB (reads from asset if omitted)."),
    storage_override: int | None = typer.Option(None, "--storage", help="Storage in GB (reads from asset if omitted)."),
    touch_override: bool | None = typer.Option(None, "--touch-screen/--no-touch-screen", help="Has touch screen (reads from asset if omitted)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print price without writing to Snipe-IT."),
) -> None:
    """Calculate the sale price and write it to the asset."""
    cfg = _require_config()
    client = get_client()

    asset = _resolve_asset(client, id, tag, serial)
    asset_id = asset.id
    asset_tag = asset.asset_tag or "?"

    # ── Resolve specs from asset or overrides ─────────────────────────────
    # RAM
    if ram_override is not None:
        ram = float(ram_override)
    else:
        raw = _get_custom_field_value(asset, cfg.custom_fields.ram)
        if raw:
            try:
                ram = float(raw)
            except ValueError:
                console.print(f"[red]Error:[/red] Cannot parse RAM value '{raw}' from asset. Pass --ram.")
                raise typer.Exit(1) from None
        else:
            console.print("[red]Error:[/red] Asset has no RAM value. Pass --ram.")
            raise typer.Exit(1)

    # Storage
    if storage_override is not None:
        storage = float(storage_override)
    else:
        raw = _get_custom_field_value(asset, cfg.custom_fields.storage)
        if raw:
            try:
                storage = float(raw)
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
    cat_name = category.get("name", "") if isinstance(category, dict) else ""
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
        ram=ram,
        storage=storage,
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
        table.add_row("RAM", f"{breakdown.ram} GB", str(breakdown.ram_points))
        table.add_row("Storage", f"{breakdown.storage} GB", str(breakdown.storage_points))
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
        _set_custom_fields(
            asset=asset,
            cfg=cfg,
            passmark=passmark_score,
            sale_price=float(breakdown.final_price),
        )
        if asset.pending_custom_fields():
            asset.save()
        console.print(
            f"[green]✓[/green] Asset {asset_tag} updated — sale price: [bold]${breakdown.final_price}[/bold]"
        )
    except (SnipeITException, RuntimeError) as exc:
        handle_api_error(exc, entity="Asset")


@assets_app.command()
def label(
    id: AssetID = None,
    tag: AssetTag = None,
    serial: AssetSerial = None,
    output: str = typer.Option("./label.pdf", "--output", "-o", help="Where to save the PDF."),
) -> None:
    """Generate and save the label PDF for an asset."""
    _require_config()
    client = get_client()

    asset = _resolve_asset(client, id, tag, serial)
    asset_tag = asset.asset_tag

    if not asset_tag:
        console.print("[red]Error:[/red] Asset has no asset tag — cannot generate label.")
        raise typer.Exit(1)

    try:
        save_path = client.assets.labels(output, [asset_tag])
        console.print(f"[green]✓[/green] Label saved to: {save_path}")
    except SnipeITException as exc:
        handle_api_error(exc, entity="Asset")


# ── Files Commands ────────────────────────────────────────────────────────────

files_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", help="Manage files attached to an asset.")
assets_app.add_typer(files_app, name="files")


def _extract_file_rows(response: Any) -> list[dict]:
    """Pull the list of file entries out of a ``list_files`` response.

    Snipe-IT documents the response as ``{"total": N, "rows": [...]}`` and
    we trust that shape (verified against snipe-it/develop). The tiny bit of
    defensiveness here just guards against the rare occasion that an older
    server returns a bare list.
    """
    if isinstance(response, dict):
        rows = response.get("rows", [])
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    if isinstance(response, list):
        return [r for r in response if isinstance(r, dict)]
    return []


@files_app.command("list")
def list_files(
    id: AssetID = None,
    tag: AssetTag = None,
    serial: AssetSerial = None,
) -> None:
    """List all files attached to an asset."""
    _require_config()
    client = get_client()

    asset = _resolve_asset(client, id, tag, serial)
    asset_id = _require_int_id(asset)

    try:
        response = client.assets.list_files(asset_id)
    except SnipeITException as exc:
        handle_api_error(exc, entity="Asset")

    if state.json_output:
        out.print_json(data=response)
        return

    items = _extract_file_rows(response)
    if not items:
        console.print(f"[yellow]No files found for asset {asset_id}.[/yellow]")
        return

    table = Table(title=f"Files for Asset {asset_id}", show_header=True, header_style="bold cyan")
    table.add_column("File ID", style="bold")
    table.add_column("Filename")
    table.add_column("Created At")
    table.add_column("Notes")

    for item in items:
        f_id = str(item.get("id", ""))
        f_name = str(item.get("filename", "") or item.get("name", ""))
        cat_obj = item.get("created_at")
        if isinstance(cat_obj, dict):
            c_at = str(cat_obj.get("formatted", cat_obj.get("datetime", "")))
        else:
            c_at = str(cat_obj or "")

        notes = str(item.get("notes", "") or "")

        table.add_row(f_id, f_name, c_at, notes)

    out.print(table)


@files_app.command("upload")
def upload_file(
    paths: list[str] = typer.Argument(..., help="Path(s) to the file(s) to upload."),  # noqa: B008
    id: AssetID = None,
    tag: AssetTag = None,
    serial: AssetSerial = None,
    notes: str | None = typer.Option(None, "--notes", help="Optional notes for the file(s)."),
) -> None:
    """Upload one or more files to an asset."""
    _require_config()
    client = get_client()

    for p in paths:
        if not os.path.isfile(p):
            console.print(f"[red]Error:[/red] File not found: {p}")
            raise typer.Exit(1)

    asset = _resolve_asset(client, id, tag, serial)
    asset_id = _require_int_id(asset)

    try:
        response = client.assets.upload_files(asset_id, paths, notes=notes)
    except SnipeITException as exc:
        handle_api_error(exc, entity="Asset")

    if state.json_output:
        out.print_json(data=response)
    else:
        console.print(f"[green]✓[/green] Successfully uploaded {len(paths)} file(s) to asset {asset_id}.")


@files_app.command("download")
def download_file(
    file_id: int = typer.Option(..., "--file-id", help="ID of the file to download."),
    id: AssetID = None,
    tag: AssetTag = None,
    serial: AssetSerial = None,
    output: str | None = typer.Option(None, "--output", "-o", help="Save path (defaults to original filename)."),
) -> None:
    """Download a file from an asset."""
    _require_config()
    client = get_client()

    asset = _resolve_asset(client, id, tag, serial)
    asset_id = _require_int_id(asset)

    save_path = output
    if not save_path:
        # Best-effort: probe list_files to recover the original filename so
        # the download lands at ./<original>. Failures are non-fatal —
        # fall through to the deterministic synthetic name.
        try:
            response = client.assets.list_files(asset_id)
            for item in _extract_file_rows(response):
                if str(item.get("id")) == str(file_id):
                    orig = item.get("filename") or item.get("name")
                    if orig:
                        save_path = f"./{orig}"
                    break
        except SnipeITException as exc:
            console.print(f"[dim]Note: could not resolve original filename ({exc})[/dim]")
        if not save_path:
            save_path = f"./{file_id}_download"

    try:
        final_path = client.assets.download_file(asset_id, file_id, save_path)
    except SnipeITException as exc:
        handle_api_error(exc, entity="Asset")

    if state.json_output:
        out.print_json(data={"status": "success", "saved_to": final_path})
    else:
        console.print(f"[green]✓[/green] File downloaded to: {final_path}")


@files_app.command("delete")
def delete_file(
    file_id: int = typer.Option(..., "--file-id", help="ID of the file to delete."),
    id: AssetID = None,
    tag: AssetTag = None,
    serial: AssetSerial = None,
    force: bool = typer.Option(False, "--force", "-f", help="Do not prompt for confirmation."),
) -> None:
    """Delete a file from an asset."""
    _require_config()
    client = get_client()

    asset = _resolve_asset(client, id, tag, serial)
    asset_id = _require_int_id(asset)

    if not force:
        confirm = typer.confirm(f"Are you sure you want to delete file {file_id} from asset {asset_id}?")
        if not confirm:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)

    try:
        client.assets.delete_file(asset_id, file_id)
    except SnipeITException as exc:
        handle_api_error(exc, entity="Asset")

    if state.json_output:
        out.print_json(data={"status": "success"})
    else:
        console.print(f"[green]✓[/green] File {file_id} deleted from asset {asset_id}.")
