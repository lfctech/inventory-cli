"""
inventory.commands.models
=========================
``inventory models`` subcommand group: get, list, create, update, delete.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from snipeit import SnipeIT
from snipeit.exceptions import SnipeITException
from snipeit.resources.models import Model

from ..core.resolvers import resolve_category, resolve_fieldset, resolve_manufacturer, resolve_model
from ..main import state
from ._common import get_client, handle_api_error
from ._lookup import ModelID, ModelName

console = Console(stderr=True)
out = Console()

models_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")


def _resolve_target_model(client: SnipeIT, model_id: int | None, name: str | None) -> Model:
    if model_id is None and name is None:
        console.print("[red]Error:[/red] Provide either --id or --name.")
        raise typer.Exit(1)
    if model_id is not None and name is not None:
        console.print("[red]Error:[/red] Provide only one of --id or --name.")
        raise typer.Exit(1)

    try:
        if model_id is not None:
            return client.models.get(model_id)
        else:
            assert name is not None
            # resolve_model returns ID, then fetch
            resolved_id = resolve_model(client, name)
            return client.models.get(resolved_id)
    except SnipeITException as exc:
        handle_api_error(exc, entity="Model")
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None


def _extract_name(field: Any) -> str:
    """Extract the name string from a Snipe-IT nested object or plain value."""
    if isinstance(field, dict):
        return field.get("name", "") or ""
    return str(field or "")


def _model_dict(model: Model) -> dict:
    return {
        "id": model.id,
        "name": getattr(model, "name", None),
        "model_number": getattr(model, "model_number", None),
        "manufacturer": _extract_name(getattr(model, "manufacturer", None)),
        "category": _extract_name(getattr(model, "category", None)),
        "fieldset": _extract_name(getattr(model, "fieldset", None)),
        "notes": getattr(model, "notes", None),
    }


def _model_table(model: Model) -> Table:
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("ID", str(model.id or ""))
    table.add_row("Name", str(getattr(model, "name", "") or ""))
    table.add_row("Model Number", str(getattr(model, "model_number", "") or ""))
    table.add_row("Manufacturer", _extract_name(getattr(model, "manufacturer", None)))
    table.add_row("Category", _extract_name(getattr(model, "category", None)))
    table.add_row("Fieldset", _extract_name(getattr(model, "fieldset", None)))
    table.add_row("Notes", str(getattr(model, "notes", "") or ""))

    return table


@models_app.command("get")
def get_model(
    id: ModelID = None,
    name: ModelName = None,
) -> None:
    """Fetch and display a single model."""
    client = get_client()
    model = _resolve_target_model(client, id, name)

    if state.json_output:
        out.print_json(data=_model_dict(model))
    else:
        out.print(_model_table(model))


@models_app.command("list")
def list_models(
    search: str | None = typer.Option(None, "--search", help="Search term for models."),
    limit: int = typer.Option(50, "--limit", help="Maximum number of models to return."),
) -> None:
    """List models.

    Uses ``list_all`` so requests larger than the server-side page size
    (typically 50) paginate transparently rather than silently truncating.
    """
    client = get_client()
    try:
        results = list(client.models.list_all(search=search, limit=limit))
    except SnipeITException as exc:
        handle_api_error(exc, entity="Model")

    if state.json_output:
        out.print_json(data=[_model_dict(m) for m in results])
        return

    if not results:
        console.print("[yellow]No models found.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Model Number")
    table.add_column("Manufacturer")
    table.add_column("Category")

    for m in results:
        table.add_row(
            str(m.id or ""),
            str(getattr(m, "name", "") or ""),
            str(getattr(m, "model_number", "") or ""),
            _extract_name(getattr(m, "manufacturer", None)),
            _extract_name(getattr(m, "category", None)),
        )
    out.print(table)


@models_app.command("create")
def create_model(
    name: str = typer.Option(..., "--name", help="Model name."),
    category: str = typer.Option(..., "--category", help="Category name (resolved to ID via API)."),
    manufacturer: str = typer.Option(..., "--manufacturer", help="Manufacturer name (resolved to ID via API)."),
    fieldset: str | None = typer.Option(None, "--fieldset", help="Fieldset name (resolved to ID via API)."),
    model_number: str | None = typer.Option(None, "--model-number", help="Model number."),
    notes: str | None = typer.Option(None, "--notes", help="Notes."),
) -> None:
    """Create a new model in Snipe-IT."""
    client = get_client()

    try:
        cat_id = resolve_category(client, category)
        mfg_id = resolve_manufacturer(client, manufacturer)
        fs_id = resolve_fieldset(client, fieldset) if fieldset is not None else None
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None

    payload: dict[str, Any] = {
        "name": name,
        "category_id": cat_id,
        "manufacturer_id": mfg_id,
    }
    if fs_id is not None:
        payload["fieldset_id"] = fs_id
    if model_number is not None:
        payload["model_number"] = model_number
    if notes is not None:
        payload["notes"] = notes

    try:
        created = client.models.create(**payload)
        if created.id is None:
            console.print("[red]Error:[/red] Model was created but the server returned no ID.")
            raise typer.Exit(1)
        model = client.models.get(int(created.id))
    except SnipeITException as exc:
        handle_api_error(exc, entity="Model")

    if state.json_output:
        out.print_json(data=_model_dict(model))
    else:
        console.print("[green]✓[/green] Model created.")
        out.print(_model_table(model))


@models_app.command("update")
def update_model(
    id: ModelID = None,
    name: ModelName = None,
    new_name: str | None = typer.Option(None, "--new-name", help="New model name."),
    category: str | None = typer.Option(None, "--category", help="Category name (resolved to ID via API)."),
    manufacturer: str | None = typer.Option(None, "--manufacturer", help="Manufacturer name (resolved to ID via API)."),
    fieldset: str | None = typer.Option(None, "--fieldset", help="Fieldset name (resolved to ID via API)."),
    model_number: str | None = typer.Option(None, "--model-number", help="Model number."),
    notes: str | None = typer.Option(None, "--notes", help="Notes."),
) -> None:
    """Update one or more fields on an existing model."""
    client = get_client()
    target = _resolve_target_model(client, id, name)
    model_id = target.id
    if model_id is None:
        console.print("[red]Error:[/red] Resolved model has no ID.")
        raise typer.Exit(1)

    payload: dict[str, Any] = {}
    if new_name is not None:
        payload["name"] = new_name
    if category is not None:
        try:
            payload["category_id"] = resolve_category(client, category)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from None
    if manufacturer is not None:
        try:
            payload["manufacturer_id"] = resolve_manufacturer(client, manufacturer)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from None
    if fieldset is not None:
        try:
            payload["fieldset_id"] = resolve_fieldset(client, fieldset)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from None
    if model_number is not None:
        payload["model_number"] = model_number
    if notes is not None:
        payload["notes"] = notes

    if not payload:
        console.print("[yellow]Warning:[/yellow] No fields to update.")
        raise typer.Exit(0)

    try:
        client.models.patch(int(model_id), **payload)
        updated = client.models.get(int(model_id))
    except SnipeITException as exc:
        handle_api_error(exc, entity="Model")

    if state.json_output:
        out.print_json(data=_model_dict(updated))
    else:
        console.print(f"[green]✓[/green] Model {model_id} updated.")
        out.print(_model_table(updated))


@models_app.command("delete")
def delete_model(
    id: ModelID = None,
    name: ModelName = None,
    force: bool = typer.Option(False, "--force", "-f", help="Do not prompt for confirmation."),
) -> None:
    """Delete a model."""
    client = get_client()
    target = _resolve_target_model(client, id, name)
    model_id = target.id
    if model_id is None:
        console.print("[red]Error:[/red] Resolved model has no ID.")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Are you sure you want to delete model {model_id} ({getattr(target, 'name', '')})?")
        if not confirm:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)

    try:
        client.models.delete(int(model_id))
    except SnipeITException as exc:
        handle_api_error(exc, entity="Model")

    if state.json_output:
        out.print_json(data={"status": "success"})
    else:
        console.print(f"[green]✓[/green] Model {model_id} deleted.")
