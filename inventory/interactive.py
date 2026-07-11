"""Guided interactive workflows for common inventory operations."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer
from click import Abort
from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from rich.table import Table
from snipeit.exceptions import (
    SnipeITAuthenticationError,
    SnipeITClientError,
    SnipeITException,
    SnipeITNotFoundError,
    SnipeITServerError,
    SnipeITTimeoutError,
    SnipeITValidationError,
)
from snipeit.resources.assets import Asset

from .application import InventoryService, NewModel, TransactionError
from .config import DEFAULT_CONFIG_TEMPLATE, _xdg_config_path
from .console import console
from .main import state

BACK = object()
_inquirer: Any = inquirer
_current_page: tuple[str, str | None] | None = None


def _streams_are_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _is_tty() -> bool:
    return _streams_are_tty()


def _enhanced_prompts() -> bool:
    """Use full-screen prompts only with a real terminal (tests use pipes)."""
    return _streams_are_tty()


def _page(title: str, subtitle: str | None = None) -> None:
    global _current_page

    _current_page = (title, subtitle)
    _redraw_page()


def _redraw_page() -> None:
    if _enhanced_prompts():
        console.clear()
    if _current_page is None:
        return
    title, subtitle = _current_page
    console.print("[bold cyan]INVENTORY[/bold cyan]  [dim]Snipe-IT guided mode[/dim]")
    console.print(f"[bold]{title}[/bold]")
    if subtitle:
        console.print(f"[dim]{subtitle}[/dim]")
    console.print()


def _prompt(text: str, *, default: str | None = None, allow_empty: bool = False) -> str | object:
    while True:
        if _enhanced_prompts():
            value = _inquirer.text(
                message=text,
                default=default or "",
                instruction="(:back to go back)",
                mandatory=not allow_empty,
                mandatory_message="Enter a value, or type :back.",
                qmark=">",
                amark="✓",
            ).execute()
        else:
            value = typer.prompt(text, default=default, show_default=default is not None)
        value = str(value).strip()
        if value == ":back":
            if _enhanced_prompts():
                _redraw_page()
            return BACK
        if value or allow_empty:
            if _enhanced_prompts():
                _redraw_page()
            return value
        console.print("[yellow]A value is required. Enter :back to go back.[/yellow]")


def _choose(
    title: str,
    options: list[str],
    *,
    back: bool = True,
    clear: bool = True,
    subtitle: str | None = None,
    fuzzy: bool = False,
) -> int | None:
    if clear:
        _page(title, subtitle)
    if _enhanced_prompts():
        choices = [Choice(index, name=option) for index, option in enumerate(options)]
        if back:
            choices.append(Choice(None, name="← Back"))
        use_fuzzy = fuzzy
        prompt = _inquirer.fuzzy if use_fuzzy else _inquirer.select
        kwargs: dict[str, Any] = {
            "message": "Choose an option",
            "choices": choices,
            "instruction": "(↑/↓ move • Enter select)",
            "pointer": ">",
            "qmark": ">",
            "amark": "✓",
            "cycle": True,
            "raise_keyboard_interrupt": True,
        }
        if use_fuzzy:
            kwargs["border"] = True
            kwargs["info"] = False
        return prompt(**kwargs).execute()

    if not clear:
        console.print(f"\n[bold]{title}[/bold]")
    for index, option in enumerate(options, 1):
        console.print(f"  {index}. {option}")
    if back:
        console.print("  0. Back")
    while True:
        raw = typer.prompt("Choose", default="0" if back else "1")
        if raw == ":back" or (back and raw == "0"):
            return None
        try:
            selected = int(raw)
        except ValueError:
            selected = -1
        if 1 <= selected <= len(options):
            return selected - 1
        console.print("[yellow]Choose one of the listed numbers.[/yellow]")


def _confirm(message: str, *, default: bool = False) -> bool:
    if _enhanced_prompts():
        return bool(
            _inquirer.confirm(
                message=message,
                default=default,
                instruction="(Y/n)" if default else "(y/N)",
                qmark=">",
                amark="✓",
            ).execute()
        )
    return typer.confirm(message, default=default)


def _name(item: Any) -> str:
    return str(getattr(item, "name", "") or "")


def _nested_name(value: Any) -> str:
    return str(value.get("name", "") if isinstance(value, dict) else value or "")


class InteractiveSession:
    def __init__(self, service: InventoryService) -> None:
        self.service = service
        assert state.config is not None
        self.config = state.config

    def run(self) -> None:
        while True:
            choice = _choose(
                "Inventory",
                ["Find an asset", "Add an asset", "Update an asset", "Save an asset label", "Exit"],
                back=False,
            )
            if choice == 4:
                return
            while True:
                try:
                    if choice == 0:
                        self.find_asset()
                    elif choice == 1:
                        self.add_asset()
                    elif choice == 2:
                        self.update_asset()
                    elif choice == 3:
                        self.label_asset()
                    break
                except TransactionError as exc:
                    self._print_transaction_error(exc)
                    break
                except SnipeITAuthenticationError as exc:
                    console.print(f"[red]Error:[/red] {_api_message(exc)}")
                    break
                except SnipeITException as exc:
                    console.print(f"[red]Error:[/red] {_api_message(exc)}")
                    retry = _choose("What next?", ["Retry this workflow"], clear=False)
                    if retry is None:
                        break
                except (ValueError, RuntimeError) as exc:
                    console.print(f"[red]Error:[/red] {exc}")
                    retry = _choose("What next?", ["Retry this workflow"], clear=False)
                    if retry is None:
                        break

    def _print_transaction_error(self, exc: TransactionError) -> None:
        if isinstance(exc.cause, SnipeITException):
            message = _api_message(exc.cause)
        else:
            message = str(exc.cause)
        console.print(f"[red]Error:[/red] {message}")
        if exc.rollback_errors:
            for error in exc.rollback_errors:
                console.print(f"[yellow]Rollback warning:[/yellow] {error}")
        else:
            console.print("[dim]Any model/manufacturer created by this operation was rolled back.[/dim]")

    def lookup(self) -> Asset | None:
        _page("Find an asset", "Scan a barcode or enter an exact asset tag or serial number.")
        while True:
            identifier = _prompt("Scan or enter asset tag/serial")
            if identifier is BACK:
                return None
            console.print("[dim]Searching by asset tag and serial…[/dim]")
            matches = self.service.find_asset(str(identifier)).unique
            if not matches:
                console.print("[yellow]No asset found. Try again or enter :back.[/yellow]")
                continue
            if len(matches) == 1:
                return matches[0]
            selected = _choose(
                "Both an asset tag and serial matched. Choose the asset",
                [_asset_label(asset) for asset in matches],
            )
            return None if selected is None else matches[selected]

    def find_asset(self) -> None:
        asset = self.lookup()
        if asset is None:
            return
        while True:
            _page("Asset details", _asset_label(asset))
            _print_asset(asset, self.config)
            choice = _choose("What next?", ["Update this asset", "Save its label"], clear=False)
            if choice is None:
                return
            if choice == 0:
                asset = self.edit_asset(asset)
            else:
                self.save_label(asset)

    def add_asset(self) -> None:
        model = self.pick_model()
        if model is BACK:
            return
        status = self.pick_resource("status_labels", "Search status labels", allow_create=False)
        if status is BACK:
            return
        _page("Add an asset", "Snipe-IT will assign the asset tag automatically.")
        serial_value = _prompt("Scan or enter serial number", allow_empty=True)
        if serial_value is BACK:
            return
        serial = str(serial_value) or None
        if serial is None and not _confirm("Serial is blank. Create the asset anyway?", default=False):
            return

        rows = [
            ("Model", model.name if isinstance(model, NewModel) else _name(model)),
            ("Status", _name(status)),
            ("Serial", serial or "(blank)"),
            ("Asset tag", "Auto-assigned by Snipe-IT"),
        ]
        if isinstance(model, NewModel):
            rows[1:1] = [
                ("New manufacturer", model.manufacturer_display or "(existing)"),
                ("Category", model.category_name or str(model.category_id)),
                ("Fieldset", model.fieldset_name or "(none)"),
                ("Model number", model.model_number or "(blank)"),
                ("Model notes", model.notes or "(blank)"),
            ]
        _page("Review new asset", "Nothing has been written yet.")
        _print_review("Create Asset", rows)
        if not _confirm("Create this asset?", default=False):
            return
        kwargs: dict[str, Any] = {"status_id": int(status.id), "serial": serial}
        if isinstance(model, NewModel):
            kwargs["new_model"] = model
        else:
            kwargs["model_id"] = int(model.id)
        asset, created = self.service.create_asset(**kwargs)
        for error in created.rollback_errors:
            console.print(f"[yellow]{error}[/yellow]")
        _page("Asset created")
        console.print(f"[green]✓[/green] Asset created: [bold]{asset.asset_tag}[/bold]")
        self._after_add(asset)

    def _after_add(self, asset: Asset) -> None:
        while True:
            choice = _choose("What next?", ["Save its label", "View the full asset"], clear=False)
            if choice is None:
                return
            if choice == 0:
                self.save_label(asset)
            else:
                _page("Asset details", _asset_label(asset))
                _print_asset(asset, self.config)

    def update_asset(self) -> None:
        asset = self.lookup()
        if asset is not None:
            _page("Update asset", _asset_label(asset))
            _print_asset(asset, self.config)
            self.edit_asset(asset)

    def edit_asset(self, asset: Asset) -> Asset:
        while True:
            changes: dict[str, Any] = {}
            review_values: dict[str, str] = {}
            model: Any = None
            while True:
                options = [
                    "Model", "Status", "Name", "CPU", "RAM", "Storage",
                    "Touch screen", "PassMark", "Sale price", "Review changes",
                ]
                staged = len(review_values)
                choice = _choose(
                    "Update asset",
                    options,
                    subtitle=f"{_asset_label(asset)}  •  {staged} staged change{'s' if staged != 1 else ''}",
                )
                if choice is None:
                    return asset
                if choice == 9:
                    break
                key = ("model", "status_id", "name", "cpu", "ram", "storage", "touch_screen", "passmark", "sale_price")[choice]
                if key == "model":
                    picked = self.pick_model()
                    if picked is not BACK:
                        model = picked
                        review_values[key] = picked.name if isinstance(picked, NewModel) else _name(picked)
                    continue
                if key == "status_id":
                    picked = self.pick_resource("status_labels", "Search status labels", allow_create=False)
                    if picked is not BACK:
                        changes[key] = int(picked.id)
                        review_values[key] = _name(picked)
                    continue
                value = self._edit_value(key)
                if value is not BACK:
                    changes[key] = value
                    review_values[key] = str(value) if value != "" else "(clear)"

            if not changes and model is None:
                console.print("[yellow]No changes selected.[/yellow]")
                continue
            _page("Review changes", "Nothing has been written yet.")
            _print_update_review(asset, self.config, review_values)
            if not _confirm("Save these changes?", default=False):
                continue
            kwargs: dict[str, Any] = {"changes": changes}
            if isinstance(model, NewModel):
                kwargs["new_model"] = model
            elif model is not None:
                kwargs["model_id"] = int(model.id)
            asset, created = self.service.update_asset(asset, self.config, **kwargs)
            for error in created.rollback_errors:
                console.print(f"[yellow]{error}[/yellow]")
            _page("Asset updated", _asset_label(asset))
            console.print(f"[green]✓[/green] Asset [bold]{asset.asset_tag or asset.id}[/bold] updated.")
            _print_asset(asset, self.config)
            action = _choose("What next?", ["Save its label", "Make another update"], clear=False)
            if action is None:
                return asset
            if action == 0:
                self.save_label(asset)
                return asset

    def _edit_value(self, key: str) -> str | int | float | object:
        if key == "touch_screen":
            choice = _choose("Touch screen", ["Yes", "No", "Clear value"])
            return BACK if choice is None else ("1", "0", "")[choice]
        action = _choose(key.replace("_", " ").title(), ["Enter new value", "Clear value"])
        if action is None:
            return BACK
        if action == 1:
            return ""
        while True:
            value = _prompt("New value")
            if value is BACK:
                return BACK
            try:
                if key in {"ram", "storage", "passmark"}:
                    return int(str(value))
                if key == "sale_price":
                    return float(str(value))
                return str(value)
            except ValueError:
                console.print("[yellow]Enter a numeric value, or :back to cancel this field.[/yellow]")

    def label_asset(self) -> None:
        asset = self.lookup()
        if asset is not None:
            _page("Save asset label", _asset_label(asset))
            _print_asset(asset, self.config)
            if _confirm("Save a label for this asset?", default=True):
                self.save_label(asset)

    def save_label(self, asset: Asset) -> None:
        default = f"./label-{asset.asset_tag}.pdf"
        _page("Save asset label", _asset_label(asset))
        while True:
            output = _prompt("Output path", default=default)
            if output is BACK:
                return
            path = Path(str(output)).expanduser()
            if not path.exists() or _confirm(f"{path} exists. Overwrite it?", default=False):
                break
            console.print("[dim]Enter another output path, or :back to cancel.[/dim]")
        saved = self.service.save_label(asset, path)
        _page("Label saved")
        console.print(f"[green]✓[/green] Label saved to: [bold]{saved}[/bold]")

    def pick_model(self) -> Any:
        return self.pick_resource("models", "Search models", allow_create=True)

    def pick_resource(self, resource: str, prompt: str, *, allow_create: bool) -> Any:
        _page(prompt, "Type a search term. Enter :back to return.")
        query = _prompt(prompt)
        if query is BACK:
            return BACK
        console.print("[dim]Searching Snipe-IT…[/dim]")
        results = self.service.search(resource, str(query))
        labels = [_resource_label(item) for item in results]
        if allow_create:
            labels.append("Create a new model")
        if not labels:
            console.print("[yellow]No matches found.[/yellow]")
            return BACK
        selected = _choose(
            "Matches",
            labels,
            subtitle="Type to filter • ↑/↓ move • Enter select",
            fuzzy=True,
        )
        if selected is None:
            return BACK
        if allow_create and selected == len(results):
            return self.new_model(str(query))
        return results[selected]

    def new_model(self, suggested_name: str) -> NewModel | object:
        _page("Create a new model", "The model will be created only after the final asset review.")
        name = _prompt("Model name", default=suggested_name)
        if name is BACK:
            return BACK
        manufacturer = self.pick_manufacturer()
        manufacturer_id: int | None = None
        manufacturer_name: str | None = None
        if manufacturer is BACK:
            return BACK
        if isinstance(manufacturer, str):
            manufacturer_name = manufacturer
        else:
            manufacturer_id = int(manufacturer.id)
        category = self.pick_resource("categories", "Search categories", allow_create=False)
        if category is BACK:
            return BACK
        fieldset_id: int | None = None
        fieldset: Any = None
        if _confirm("Select a fieldset?", default=False):
            fieldset = self.pick_resource("fieldsets", "Search fieldsets", allow_create=False)
            if fieldset is not BACK:
                fieldset_id = int(fieldset.id)
        _page("Create a new model", "Optional model details. Enter :back to cancel.")
        model_number = _prompt("Model number", allow_empty=True)
        if model_number is BACK:
            return BACK
        notes = _prompt("Notes", allow_empty=True)
        if notes is BACK:
            return BACK
        return NewModel(
            name=str(name), category_id=int(category.id), manufacturer_id=manufacturer_id,
            manufacturer_name=manufacturer_name, fieldset_id=fieldset_id,
            model_number=str(model_number) or None, notes=str(notes) or None,
            category_name=_name(category),
            manufacturer_display=manufacturer_name or _name(manufacturer),
            fieldset_name=_name(fieldset) if fieldset_id is not None else None,
        )

    def pick_manufacturer(self) -> Any:
        _page("Choose manufacturer", "Search existing manufacturers or create a new one.")
        query = _prompt("Search manufacturers")
        if query is BACK:
            return BACK
        results = self.service.search("manufacturers", str(query))
        selected = _choose(
            "Matches",
            [_resource_label(item) for item in results] + ["Create a new manufacturer"],
            subtitle="Type to filter • ↑/↓ move • Enter select",
            fuzzy=True,
        )
        if selected is None:
            return BACK
        if selected == len(results):
            value = _prompt("Manufacturer name", default=str(query))
            return BACK if value is BACK else str(value)
        return results[selected]


def run_interactive() -> None:
    from .commands._common import get_client

    if state.json_output:
        raise typer.BadParameter("--json cannot be used with interactive mode.")
    if not _is_tty():
        raise typer.BadParameter("Interactive mode requires a terminal.")
    if state.config is None:
        target = _xdg_config_path()
        console.print("[yellow]No config.toml was found.[/yellow]")
        if _confirm(f"Create a starter config at {target}?", default=True):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(DEFAULT_CONFIG_TEMPLATE)
            console.print(f"[green]✓[/green] Config written to: {target}")
        raise typer.Exit(1)
    if not state.api_key:
        console.print("[red]Error:[/red] Set SNIPEIT_API_KEY or pass --api-key before using interactive mode.")
        raise typer.Exit(1)
    try:
        InteractiveSession(InventoryService(get_client())).run()
    except (KeyboardInterrupt, Abort):
        console.print("\n[yellow]Exiting interactive mode.[/yellow]")


def _api_message(exc: SnipeITException) -> str:
    if isinstance(exc, SnipeITAuthenticationError):
        return "Authentication failed. Check your API key."
    if isinstance(exc, SnipeITNotFoundError):
        return "Resource not found."
    if isinstance(exc, SnipeITValidationError):
        return f"Validation failed — {exc}"
    if isinstance(exc, SnipeITServerError):
        return "Snipe-IT server error. Try again later."
    if isinstance(exc, SnipeITTimeoutError):
        return "Request timed out."
    if isinstance(exc, SnipeITClientError):
        return f"Client error — {exc}"
    return str(exc)


def _resource_label(item: Any) -> str:
    details = [str(getattr(item, "model_number", "") or ""), _nested_name(getattr(item, "manufacturer", None))]
    suffix = " · ".join(value for value in details if value)
    return f"{_name(item)}{f' — {suffix}' if suffix else ''}"


def _asset_label(asset: Asset) -> str:
    return f"{asset.asset_tag or '(no tag)'} — {asset.serial or '(no serial)'} — {_nested_name(asset.model)}"


def _custom(asset: Asset, label: str) -> str:
    value = asset.get_custom_field(label)
    return "" if value is None else str(value)


def _print_asset(asset: Asset, config: Any) -> None:
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    fields = [
        ("Asset Tag", asset.asset_tag), ("Serial", asset.serial), ("Name", asset.name),
        ("Model", _nested_name(asset.model)), ("Status", _nested_name(getattr(asset, "status_label", None))),
        ("CPU", _custom(asset, config.custom_fields.cpu_model)),
        ("RAM", _custom(asset, config.custom_fields.ram)),
        ("Storage", _custom(asset, config.custom_fields.storage)),
        ("Touch Screen", _custom(asset, config.custom_fields.touch_screen)),
        ("PassMark", _custom(asset, config.custom_fields.cpu_passmark)),
        ("Sale Price", _custom(asset, config.custom_fields.sale_price)),
    ]
    for label, value in fields:
        table.add_row(label, str(value or ""))
    console.print(table)


def _print_review(title: str, rows: list[tuple[str, str]]) -> None:
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("New value")
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)


def _print_update_review(asset: Asset, config: Any, values: dict[str, str]) -> None:
    current = {
        "model": _nested_name(asset.model),
        "status_id": _nested_name(getattr(asset, "status_label", None)),
        "name": str(asset.name or ""),
        "cpu": _custom(asset, config.custom_fields.cpu_model),
        "ram": _custom(asset, config.custom_fields.ram),
        "storage": _custom(asset, config.custom_fields.storage),
        "touch_screen": _custom(asset, config.custom_fields.touch_screen),
        "passmark": _custom(asset, config.custom_fields.cpu_passmark),
        "sale_price": _custom(asset, config.custom_fields.sale_price),
    }
    labels = {"status_id": "Status", "touch_screen": "Touch screen", "sale_price": "Sale price"}
    table = Table(title="Update Asset", show_header=True, header_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Current")
    table.add_column("New value")
    for key, value in values.items():
        table.add_row(labels.get(key, key.replace("_", " ").title()), current[key], value)
    console.print(table)
