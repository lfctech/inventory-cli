"""Guided interactive workflows for common inventory operations."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, TypeGuard, cast

import typer
from click import Abort
from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from prompt_toolkit.keys import Keys
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

from .application import InventoryService, MutationOutcome, NewModel, TransactionError
from .config import DEFAULT_CONFIG_TEMPLATE, _xdg_config_path
from .console import console
from .main import state


class _BackResult:
    """Marker returned by guided prompts when Escape requests Back."""


BACK = _BackResult()
_inquirer: Any = inquirer
_current_page: tuple[str, str | None, str | None] | None = None

_SELECT_HELP = "↑/↓ move • Enter select • Esc back"
_SELECT_NO_BACK_HELP = "↑/↓ move • Enter select • Ctrl+C exit"
_FUZZY_HELP = "Type to filter • ↑/↓ move • Enter select • Esc back"
_TEXT_HELP = "Enter submit • Esc back"
_CONFIRM_HELP = "Y/N choose • Esc back"
_CONFIRM_NO_BACK_HELP = "Y/N choose • Ctrl+C exit"
_ESCAPE_TIMEOUT_SECONDS = 0.05
_CUSTOM_EDIT_KEYS = {"cpu", "ram", "storage", "touch_screen", "passmark", "sale_price"}
_CUSTOM_EDIT_ORDER = ("cpu", "ram", "storage", "touch_screen", "passmark", "sale_price")
_EDIT_LABELS = {
    "model": "Model",
    "status_id": "Status",
    "name": "Name",
    "cpu": "CPU",
    "ram": "RAM",
    "storage": "Storage",
    "touch_screen": "Touch screen",
    "passmark": "PassMark",
    "sale_price": "Sale price",
    "review": "Review changes",
}
_NEW_MODEL_REVIEW_KEYS = (
    ("new_model_manufacturer", "New manufacturer"),
    ("new_model_category", "Category"),
    ("new_model_fieldset", "Fieldset"),
    ("new_model_number", "Model number"),
    ("new_model_notes", "Model notes"),
)


@dataclass
class AddAssetDraft:
    model: Any = None
    status: Any = None
    serial: str | None = None


@dataclass
class AssetEditDraft:
    changes: dict[str, Any] = field(default_factory=dict)
    review_values: dict[str, str] = field(default_factory=dict)
    model: Any = None


class AddStep(Enum):
    MODEL = auto()
    STATUS = auto()
    SERIAL = auto()
    REVIEW = auto()


class ModelStep(Enum):
    NAME = auto()
    MANUFACTURER = auto()
    CATEGORY = auto()
    FIELDSET = auto()
    MODEL_NUMBER = auto()
    NOTES = auto()


def _streams_are_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _is_tty() -> bool:
    return _streams_are_tty()


def _enhanced_prompts() -> bool:
    """Use full-screen prompts only with a real terminal (tests use pipes)."""
    return _streams_are_tty()


def _page(
    title: str,
    subtitle: str | None = None,
    *,
    controls: str | None = None,
    clear: bool = True,
) -> None:
    global _current_page

    _current_page = (title, subtitle, controls)
    _redraw_page(clear=clear)


def _redraw_page(*, clear: bool = True) -> None:
    if clear and _enhanced_prompts():
        console.clear()
    if _current_page is None:
        return
    title, subtitle, controls = _current_page
    console.print(f"[bold]{title}[/bold]")
    if controls:
        console.print(f"[dim]{controls}[/dim]")
    if subtitle:
        console.print(f"[dim]{subtitle}[/dim]")
    console.print()


def _tune_escape_timeout(prompt: Any) -> None:
    """Reduce Prompt Toolkit's default wait before treating Esc as a key."""

    application = getattr(prompt, "application", None)
    if application is None:
        session = getattr(prompt, "_session", None)
        application = getattr(session, "app", None)
    if application is None:
        return
    application.ttimeoutlen = _ESCAPE_TIMEOUT_SECONDS
    application.timeoutlen = _ESCAPE_TIMEOUT_SECONDS


def _install_back_binding(prompt: Any) -> None:
    """Make Escape return the workflow's internal back result.

    InquirerPy exposes key bindings as prompt-instance state. Registering on the
    instance keeps the standard Ctrl+C interrupt binding intact while adding the
    same Escape behavior to text, select, fuzzy, and confirmation prompts.
    """

    _tune_escape_timeout(prompt)
    register_kb = cast(Any, getattr(prompt, "register_kb", None))
    if not callable(register_kb):
        return

    def _handle_back(event: Any) -> None:
        status = getattr(prompt, "status", None)
        if isinstance(status, dict):
            status["answered"] = True
            status["result"] = BACK
        event.app.exit(result=BACK)

    cast(Any, register_kb(Keys.Escape))(_handle_back)


def _prompt(text: str, *, default: str | None = None, allow_empty: bool = False) -> str | object:
    while True:
        if _enhanced_prompts():
            prompt = _inquirer.text(
                message=text,
                default=default or "",
                instruction="",
                mandatory=not allow_empty,
                mandatory_message="Enter a value.",
                qmark=">",
                amark="✓",
            )
            _install_back_binding(prompt)
            value = prompt.execute()
        else:
            value = typer.prompt(text, default=default, show_default=default is not None)
        if value is BACK:
            return BACK
        value = str(value).strip()
        if value == ":back":
            return BACK
        if value == "\x1b":
            return BACK
        if value or allow_empty:
            return value
        console.print("[yellow]A value is required.[/yellow]")


def _choose(
    title: str,
    options: list[str],
    *,
    back: bool = True,
    clear: bool = True,
    subtitle: str | None = None,
    fuzzy: bool = False,
    controls: str | None = None,
) -> int | None | _BackResult:
    if controls is None:
        if fuzzy:
            controls = _FUZZY_HELP if back else _SELECT_NO_BACK_HELP
        else:
            controls = _SELECT_HELP if back else _SELECT_NO_BACK_HELP
    if clear:
        _page(
            title,
            subtitle,
            controls=controls,
        )
    else:
        _page(
            title,
            subtitle,
            controls=controls,
            clear=False,
        )
    if _enhanced_prompts():
        choices = [Choice(index, name=option) for index, option in enumerate(options)]
        use_fuzzy = fuzzy
        prompt = _inquirer.fuzzy if use_fuzzy else _inquirer.select
        kwargs: dict[str, Any] = {
            "message": "Choose an option",
            "choices": choices,
            "instruction": "",
            "pointer": ">",
            "qmark": ">",
            "amark": "✓",
            "cycle": True,
            "raise_keyboard_interrupt": True,
        }
        if use_fuzzy:
            kwargs["border"] = True
            kwargs["info"] = False
        prompt_instance = prompt(**kwargs)
        if back:
            _install_back_binding(prompt_instance)
        return prompt_instance.execute()

    for index, option in enumerate(options, 1):
        console.print(f"  {index}. {option}")
    while True:
        raw = typer.prompt("Choose", default="1")
        if raw in {":back", "\x1b"} or (back and raw == "0"):
            return BACK if back else None
        try:
            selected = int(raw)
        except ValueError:
            selected = -1
        if 1 <= selected <= len(options):
            return selected - 1
        console.print("[yellow]Choose one of the listed numbers.[/yellow]")


def _confirm(
    message: str,
    *,
    default: bool = False,
    back: bool = True,
) -> bool | object:
    if _enhanced_prompts():
        prompt = _inquirer.confirm(
            message=message,
            default=default,
            instruction="",
            qmark=">",
            amark="✓",
        )
        if back:
            _install_back_binding(prompt)
        return prompt.execute()
    return typer.confirm(message, default=default)


def _is_back(value: int | None | _BackResult) -> TypeGuard[None | _BackResult]:
    """Accept both the current Escape result and the legacy fallback result."""

    return value is BACK or value is None


def _name(item: Any) -> str:
    return str(getattr(item, "name", "") or "")


def _nested_name(value: Any) -> str:
    return str(value.get("name", "") if isinstance(value, dict) else value or "")


def _model_name(model: Any) -> str:
    return model.name if isinstance(model, NewModel) else _name(model)


def _clear_edit_draft(draft: AssetEditDraft) -> None:
    draft.changes.clear()
    draft.review_values.clear()
    draft.model = None


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
                    _page("Retry workflow", controls=_CONFIRM_HELP, clear=False)
                    if _confirm("Retry this workflow?", default=True) is not True:
                        break
                except (ValueError, RuntimeError) as exc:
                    console.print(f"[red]Error:[/red] {exc}")
                    _page("Retry workflow", controls=_CONFIRM_HELP, clear=False)
                    if _confirm("Retry this workflow?", default=True) is not True:
                        break

    def _print_transaction_error(self, exc: TransactionError) -> None:
        if isinstance(exc.cause, SnipeITException):
            message = _api_message(exc.cause)
        else:
            message = str(exc.cause)
        console.print(f"[red]Error:[/red] {message}")
        if exc.outcome is MutationOutcome.COMPLETED:
            asset_text = f" Asset ID {exc.asset_id} is known." if exc.asset_id is not None else ""
            console.print(
                "[yellow]The asset update was saved; do not retry this workflow."
                f"{asset_text} Reconcile the displayed state in Snipe-IT if needed.[/yellow]"
            )
        elif exc.outcome is MutationOutcome.AMBIGUOUS:
            asset_text = f" Asset ID {exc.asset_id} is known." if exc.asset_id is not None else ""
            resource_ids: list[str] = []
            if exc.created is not None:
                if exc.created.model_id is not None:
                    resource_ids.append(f"model {exc.created.model_id}")
                if exc.created.manufacturer_id is not None:
                    resource_ids.append(f"manufacturer {exc.created.manufacturer_id}")
            related_text = (
                f" Known related records: {', '.join(resource_ids)}."
                if resource_ids
                else ""
            )
            console.print(
                "[yellow]Mutation outcome is ambiguous; do not retry this workflow."
                f"{asset_text}{related_text} Reconcile the asset and any related records "
                "in Snipe-IT first.[/yellow]"
            )
        if exc.rollback_errors:
            for error in exc.rollback_errors:
                console.print(f"[yellow]Rollback warning:[/yellow] {error}")
        elif exc.outcome is MutationOutcome.ROLLED_BACK:
            console.print(
                "[dim]Any model/manufacturer created by this operation was rolled back.[/dim]"
            )

    def lookup(self) -> Asset | None:
        _page(
            "Find an asset",
            "Exact tag or serial match only",
            controls=_TEXT_HELP,
        )
        identifier_text: str | None = None
        while True:
            identifier = _prompt(
                "Scan or enter asset tag/serial",
                default=identifier_text,
            )
            if identifier is BACK:
                return None
            identifier_text = str(identifier)
            console.print("[dim]Searching by asset tag and serial…[/dim]")
            try:
                matches = self.service.find_asset(identifier_text).unique
            except SnipeITAuthenticationError:
                raise
            except Exception as exc:
                message = _api_message(exc) if isinstance(exc, SnipeITException) else str(exc)
                console.print(f"[red]Error:[/red] {message}")
                _page(
                    "Find an asset",
                    f"Search failed for {identifier_text}; retry or press Esc to cancel",
                    controls=_TEXT_HELP,
                    clear=False,
                )
                continue
            if not matches:
                console.print("[yellow]No asset found. Try again.[/yellow]")
                continue
            if len(matches) == 1:
                return matches[0]
            selected = _choose(
                "Both an asset tag and serial matched. Choose the asset",
                [_asset_label(asset) for asset in matches],
            )
            if _is_back(selected):
                _page(
                    "Find an asset",
                    "Exact tag or serial match only",
                    controls=_TEXT_HELP,
                )
                continue
            assert isinstance(selected, int)
            return matches[selected]

    def find_asset(self) -> None:
        while True:
            asset = self.lookup()
            if asset is None:
                return
            draft = AssetEditDraft()
            while True:
                _page("Asset details", _asset_label(asset))
                _print_asset(asset, self.config)
                choice = _choose("What next?", ["Update this asset", "Save its label"], clear=False)
                if _is_back(choice):
                    break
                if choice == 0:
                    edited = self.edit_asset(asset, draft)
                    if isinstance(edited, Asset):
                        asset = edited
                        draft = AssetEditDraft()
                else:
                    self._save_label_with_recovery(asset)

    def add_asset(self) -> None:
        draft = AddAssetDraft()
        step = AddStep.MODEL
        while True:
            if step is AddStep.MODEL:
                had_model = draft.model is not None
                if had_model:
                    selected = _choose(
                        "Choose model",
                        [f"Continue with {_model_name(draft.model)}", "Choose a different model"],
                    )
                    if _is_back(selected):
                        return
                    if selected == 0:
                        step = AddStep.STATUS
                        continue
                model = self.pick_model()
                if model is BACK:
                    if had_model:
                        continue
                    return
                draft.model = model
                step = AddStep.STATUS
                continue

            if step is AddStep.STATUS:
                had_status = draft.status is not None
                if had_status:
                    selected = _choose(
                        "Choose status",
                        [f"Continue with {_name(draft.status)}", "Choose a different status"],
                    )
                    if _is_back(selected):
                        step = AddStep.MODEL
                        continue
                    if selected == 0:
                        step = AddStep.SERIAL
                        continue
                status = self.pick_resource(
                    "status_labels", "Search status labels", allow_create=False
                )
                if status is BACK:
                    if not had_status:
                        step = AddStep.MODEL
                    continue
                draft.status = status
                step = AddStep.SERIAL
                continue

            if step is AddStep.SERIAL:
                _page(
                    "Add an asset",
                    "Snipe-IT will assign the asset tag automatically",
                    controls=_TEXT_HELP,
                )
                serial_value = _prompt(
                    "Scan or enter serial number",
                    default=draft.serial,
                    allow_empty=True,
                )
                if serial_value is BACK:
                    step = AddStep.STATUS
                    continue
                draft.serial = str(serial_value) or None
                if draft.serial is None:
                    blank = _confirm("Serial is blank. Create the asset anyway?", default=False)
                    if blank is BACK:
                        continue
                    if not blank:
                        continue
                step = AddStep.REVIEW
                continue

            rows = [
                ("Model", _model_name(draft.model)),
                ("Status", _name(draft.status)),
                ("Serial", draft.serial or "(blank)"),
                ("Asset tag", "Auto-assigned by Snipe-IT"),
            ]
            if isinstance(draft.model, NewModel):
                rows[1:1] = [
                    ("New manufacturer", draft.model.manufacturer_display or "(existing)"),
                    ("Category", draft.model.category_name or str(draft.model.category_id)),
                    ("Fieldset", draft.model.fieldset_name or "(none)"),
                    ("Model number", draft.model.model_number or "(blank)"),
                    ("Model notes", draft.model.notes or "(blank)"),
                ]
            _page("Review new asset", "Nothing has been written yet", controls=_CONFIRM_HELP)
            _print_review("Create Asset", rows)
            confirmed = _confirm("Create this asset?", default=False)
            if confirmed is BACK:
                step = AddStep.SERIAL
                continue
            if not confirmed:
                return
            break

        assert draft.status is not None and draft.model is not None
        kwargs: dict[str, Any] = {"status_id": int(draft.status.id), "serial": draft.serial}
        if isinstance(draft.model, NewModel):
            kwargs["new_model"] = draft.model
        else:
            kwargs["model_id"] = int(draft.model.id)
        asset, created = self.service.create_asset(**kwargs)
        for error in created.rollback_errors:
            console.print(f"[yellow]{error}[/yellow]")
        _page("Asset created")
        console.print(f"[green]✓[/green] Asset created: [bold]{asset.asset_tag}[/bold]")
        self._after_add(asset)

    def _after_add(self, asset: Asset) -> None:
        while True:
            choice = _choose(
                "What next?",
                ["Save its label", "View the full asset"],
                clear=False,
                controls="↑/↓ move • Enter select • Esc main menu",
            )
            if _is_back(choice):
                return
            if choice == 0:
                self._save_label_with_recovery(asset)
            else:
                _page("Asset details", _asset_label(asset))
                _print_asset(asset, self.config)

    def _save_label_with_recovery(self, asset: Asset) -> bool:
        """Save one label while retaining the asset on any recoverable error."""

        while True:
            try:
                return self.save_label(asset)
            except SnipeITException as exc:
                message = _api_message(exc)
            except (OSError, ValueError, RuntimeError) as exc:
                message = str(exc)
            console.print(f"[red]Error:[/red] {message}")
            console.print(
                f"[yellow]Asset {asset.asset_tag or asset.id} is saved, "
                "but its label was not saved.[/yellow]"
            )
            retry = _choose("Label not saved", ["Retry saving this label"], clear=False)
            if _is_back(retry):
                return False

    def update_asset(self) -> None:
        while True:
            asset = self.lookup()
            if asset is None:
                return
            draft = AssetEditDraft()
            while True:
                _page("Update asset", _asset_label(asset), controls=_CONFIRM_HELP)
                _print_asset(asset, self.config)
                if _confirm("Edit this asset?", default=True) is not True:
                    break
                edited = self.edit_asset(asset, draft)
                if edited is not BACK:
                    return

    def edit_asset(self, asset: Asset, draft: AssetEditDraft | None = None) -> Asset | object:
        draft = draft or AssetEditDraft()
        while True:
            while True:
                option_keys = ["model", "status_id", "name"]
                option_keys.extend(
                    key for key in _CUSTOM_EDIT_ORDER if self._custom_field_supported(asset, key)
                )
                option_keys.append("review")
                options = [_EDIT_LABELS[key] for key in option_keys]
                staged = len(draft.review_values)
                choice = _choose(
                    "Update asset",
                    options,
                    subtitle=f"{_asset_label(asset)}  •  {staged} staged change{'s' if staged != 1 else ''}",
                )
                if _is_back(choice):
                    return BACK
                if choice == len(option_keys) - 1:
                    break
                assert isinstance(choice, int)
                key = option_keys[choice]
                if key == "model":
                    if set(draft.changes) & _CUSTOM_EDIT_KEYS:
                        console.print(
                            "[yellow]Save the staged custom-field changes first; then choose "
                            "the model change so the target fieldset is known.[/yellow]"
                        )
                        continue
                    picked = self.pick_model()
                    if picked is not BACK:
                        draft.model = picked
                        draft.review_values[key] = _model_name(picked)
                        for review_key, _ in _NEW_MODEL_REVIEW_KEYS:
                            draft.review_values.pop(review_key, None)
                        if isinstance(picked, NewModel):
                            draft.review_values.update(_new_model_review_values(picked))
                    continue
                if key == "status_id":
                    picked = self.pick_resource(
                        "status_labels", "Search status labels", allow_create=False
                    )
                    if picked is not BACK:
                        draft.changes[key] = int(picked.id)
                        draft.review_values[key] = _name(picked)
                    continue
                if key in _CUSTOM_EDIT_KEYS and not self._custom_field_available(asset, key):
                    continue
                if key in _CUSTOM_EDIT_KEYS and draft.model is not None:
                    console.print(
                        "[yellow]Save the model change first; then edit custom fields "
                        "so the target fieldset is known.[/yellow]"
                    )
                    continue
                value = self._edit_value(key)
                if value is not BACK:
                    draft.changes[key] = value
                    draft.review_values[key] = str(value) if value != "" else "(clear)"

            if not draft.changes and draft.model is None:
                console.print("[yellow]No changes selected.[/yellow]")
                continue
            _page("Review changes", "Nothing has been written yet", controls=_CONFIRM_HELP)
            _print_update_review(asset, self.config, draft.review_values)
            confirmed = _confirm("Save these changes?", default=False)
            if confirmed is BACK or not confirmed:
                continue
            kwargs: dict[str, Any] = {"changes": draft.changes}
            if isinstance(draft.model, NewModel):
                kwargs["new_model"] = draft.model
            elif draft.model is not None:
                kwargs["model_id"] = int(draft.model.id)
            asset, created = self.service.update_asset(asset, self.config, **kwargs)
            for error in created.rollback_errors:
                console.print(f"[yellow]{error}[/yellow]")
            if created.refresh_verified:
                _page("Asset updated", _asset_label(asset))
                console.print(
                    f"[green]✓[/green] Asset [bold]{asset.asset_tag or asset.id}[/bold] updated."
                )
            else:
                _page(
                    "Asset saved; refresh not verified",
                    _asset_label(asset),
                    controls=_CONFIRM_HELP,
                )
                console.print(
                    f"[yellow]Asset [bold]{asset.asset_tag or asset.id}[/bold] was saved, "
                    "but Snipe-IT could not verify the refreshed state.[/yellow]"
                )
                self._offer_refresh(asset, created)
            _print_asset(asset, self.config)
            if not created.refresh_verified:
                return asset
            action = _choose("What next?", ["Save its label", "Make another update"], clear=False)
            if _is_back(action):
                return asset
            if action == 0:
                self._save_label_with_recovery(asset)
                return asset
            _clear_edit_draft(draft)

    def _custom_field_available(self, asset: Asset, key: str) -> bool:
        label = self._custom_field_label(key)
        custom_fields = getattr(asset, "custom_fields", None)
        if not isinstance(custom_fields, dict):
            console.print(
                "[yellow]This asset's custom-field fieldset is unavailable; "
                "refresh it before editing.[/yellow]"
            )
            return False
        if label not in custom_fields:
            console.print(
                f"[yellow]Custom field {label!r} is not available on this asset's "
                "model fieldset.[/yellow]"
            )
            return False
        return True

    def _custom_field_label(self, key: str) -> str:
        return {
            "cpu": self.config.custom_fields.cpu_model,
            "ram": self.config.custom_fields.ram,
            "storage": self.config.custom_fields.storage,
            "touch_screen": self.config.custom_fields.touch_screen,
            "passmark": self.config.custom_fields.cpu_passmark,
            "sale_price": self.config.custom_fields.sale_price,
        }[key]

    def _custom_field_supported(self, asset: Asset, key: str) -> bool:
        custom_fields = getattr(asset, "custom_fields", None)
        return isinstance(custom_fields, dict) and self._custom_field_label(key) in custom_fields

    def _offer_refresh(self, asset: Asset, created: Any) -> None:
        """Offer a read-only refresh after a successful but unverified save."""

        while not created.refresh_verified:
            action = _choose(
                "Refresh verification",
                ["Retry refresh", "Continue with saved asset"],
                clear=False,
            )
            if _is_back(action) or action == 1:
                return
            try:
                asset.refresh()
            except Exception as exc:
                created.refresh_error = str(exc)
                message = _api_message(exc) if isinstance(exc, SnipeITException) else str(exc)
                console.print(f"[yellow]Refresh still failed:[/yellow] {message}")
                continue
            created.refresh_verified = True
            created.refresh_error = None
            console.print("[green]✓[/green] Asset state refreshed from Snipe-IT.")

    def _edit_value(self, key: str) -> str | int | float | object:
        if key == "touch_screen":
            choice = _choose("Touch screen", ["Yes", "No", "Clear value"])
            if _is_back(choice):
                return BACK
            assert isinstance(choice, int)
            return ("1", "0", "")[choice]
        while True:
            action = _choose(key.replace("_", " ").title(), ["Enter new value", "Clear value"])
            if _is_back(action):
                return BACK
            if action == 1:
                return ""
            _page(key.replace("_", " ").title(), controls=_TEXT_HELP)
            while True:
                value = _prompt("New value")
                if value is BACK:
                    break
                try:
                    if key in {"ram", "storage", "passmark"}:
                        parsed = int(str(value))
                        if parsed < 0:
                            console.print("[yellow]Enter a non-negative integer.[/yellow]")
                            continue
                        return parsed
                    if key == "sale_price":
                        parsed_price = float(str(value))
                        if not math.isfinite(parsed_price) or parsed_price < 0:
                            console.print(
                                "[yellow]Enter a finite, non-negative number.[/yellow]"
                            )
                            continue
                        return parsed_price
                    return str(value)
                except ValueError:
                    console.print("[yellow]Enter a numeric value.[/yellow]")

    def label_asset(self) -> None:
        while True:
            asset = self.lookup()
            if asset is None:
                return
            while True:
                _page("Save asset label", _asset_label(asset), controls=_CONFIRM_HELP)
                _print_asset(asset, self.config)
                confirmed = _confirm("Save a label for this asset?", default=True)
                if confirmed is BACK:
                    break
                if not confirmed:
                    return
                if self._save_label_with_recovery(asset):
                    return

    def save_label(self, asset: Asset) -> bool:
        default = f"./label-{asset.asset_tag}.pdf"
        _page("Save asset label", _asset_label(asset), controls=_TEXT_HELP)
        while True:
            output = _prompt("Output path", default=default)
            if output is BACK:
                return False
            path = Path(str(output)).expanduser()
            if not path.exists():
                break
            _page("Overwrite file", str(path), controls=_CONFIRM_HELP)
            overwrite = _confirm(f"{path} exists. Overwrite it?", default=False)
            if overwrite is BACK:
                _page("Save asset label", _asset_label(asset), controls=_TEXT_HELP)
                continue
            if overwrite:
                break
            _page("Save asset label", _asset_label(asset), controls=_TEXT_HELP)
            console.print("[dim]Enter another output path.[/dim]")
        saved = self.service.save_label(asset, path)
        _page("Label saved")
        console.print(f"[green]✓[/green] Label saved to: [bold]{saved}[/bold]")
        return True

    def pick_model(self) -> Any:
        return self.pick_resource("models", "Search models", allow_create=True)

    def pick_resource(self, resource: str, prompt: str, *, allow_create: bool) -> Any:
        query_text: str | None = None
        title = prompt.removeprefix("Search ").capitalize()
        while True:
            _page(title, controls=_TEXT_HELP)
            query = _prompt(prompt, default=query_text)
            if query is BACK:
                return BACK
            query_text = str(query)
            console.print("[dim]Searching Snipe-IT…[/dim]")
            try:
                results = self.service.search(resource, query_text)
            except SnipeITAuthenticationError:
                raise
            except Exception as exc:
                message = _api_message(exc) if isinstance(exc, SnipeITException) else str(exc)
                console.print(f"[red]Error:[/red] {message}")
                _page(
                    title,
                    f"Search failed for {query_text}; retry or press Esc to cancel",
                    controls=_TEXT_HELP,
                    clear=False,
                )
                continue
            labels = [_resource_label(item) for item in results]
            if allow_create:
                labels.append("Create a new model")
            if not labels:
                console.print("[yellow]No matches found. Try again.[/yellow]")
                continue
            selected = _choose(
                "Matches",
                labels,
                fuzzy=True,
            )
            if _is_back(selected):
                return BACK
            if allow_create and selected == len(results):
                created = self.new_model(query_text)
                if created is BACK:
                    continue
                return created
            assert isinstance(selected, int)
            return results[selected]

    def new_model(self, suggested_name: str) -> NewModel | object:
        name: str | None = suggested_name
        manufacturer: Any = None
        manufacturer_id: int | None = None
        manufacturer_name: str | None = None
        category: Any = None
        fieldset_id: int | None = None
        fieldset: Any = None
        model_number: str | None = None
        notes: str | None = None
        step = ModelStep.NAME

        while True:
            if step is ModelStep.NAME:
                _page(
                    "Create a new model",
                    "The model will be created only after final review",
                    controls=_TEXT_HELP,
                )
                value = _prompt("Model name", default=name)
                if value is BACK:
                    return BACK
                name = str(value)
                step = ModelStep.MANUFACTURER
                continue
            if step is ModelStep.MANUFACTURER:
                manufacturer = self.pick_manufacturer(manufacturer)
                if manufacturer is BACK:
                    step = ModelStep.NAME
                    continue
                manufacturer_id = None
                manufacturer_name = None
                if isinstance(manufacturer, str):
                    manufacturer_name = manufacturer
                else:
                    manufacturer_id = int(manufacturer.id)
                step = ModelStep.CATEGORY
                continue
            if step is ModelStep.CATEGORY:
                if category is not None:
                    selected = _choose(
                        "Choose category",
                        [f"Continue with {_name(category)}", "Choose a different category"],
                    )
                    if _is_back(selected):
                        step = ModelStep.MANUFACTURER
                        continue
                    if selected == 0:
                        step = ModelStep.FIELDSET
                        continue
                selected_category = self.pick_resource(
                    "categories", "Search categories", allow_create=False
                )
                if selected_category is BACK:
                    if category is None:
                        step = ModelStep.MANUFACTURER
                    continue
                category = selected_category
                step = ModelStep.FIELDSET
                continue
            if step is ModelStep.FIELDSET:
                _page("Create a new model", "Fieldset is optional", controls=_CONFIRM_HELP)
                if fieldset is not None:
                    selected = _choose(
                        "Choose fieldset",
                        [
                            f"Continue with {_name(fieldset)}",
                            "Choose a different fieldset",
                            "No fieldset",
                        ],
                    )
                    if _is_back(selected):
                        step = ModelStep.CATEGORY
                        continue
                    if selected == 0:
                        step = ModelStep.MODEL_NUMBER
                        continue
                    if selected == 2:
                        fieldset = None
                        fieldset_id = None
                        step = ModelStep.MODEL_NUMBER
                        continue
                    select_fieldset: bool | object = True
                else:
                    select_fieldset = _confirm("Select a fieldset?", default=False)
                if select_fieldset is BACK:
                    step = ModelStep.CATEGORY
                    continue
                if select_fieldset:
                    selected_fieldset = self.pick_resource(
                        "fieldsets", "Search fieldsets", allow_create=False
                    )
                    if selected_fieldset is BACK:
                        continue
                    fieldset = selected_fieldset
                    fieldset_id = int(fieldset.id)
                else:
                    fieldset = None
                    fieldset_id = None
                step = ModelStep.MODEL_NUMBER
                continue
            if step is ModelStep.MODEL_NUMBER:
                _page("Create a new model", "Optional model details", controls=_TEXT_HELP)
                value = _prompt("Model number", default=model_number, allow_empty=True)
                if value is BACK:
                    step = ModelStep.FIELDSET
                    continue
                model_number = str(value) or None
                step = ModelStep.NOTES
                continue
            _page("Create a new model", "Optional model details", controls=_TEXT_HELP)
            value = _prompt("Notes", default=notes, allow_empty=True)
            if value is BACK:
                step = ModelStep.MODEL_NUMBER
                continue
            notes = str(value) or None
            break

        assert name is not None and category is not None
        return NewModel(
            name=str(name),
            category_id=int(category.id),
            manufacturer_id=manufacturer_id,
            manufacturer_name=manufacturer_name,
            fieldset_id=fieldset_id,
            model_number=model_number,
            notes=notes,
            category_name=_name(category),
            manufacturer_display=manufacturer_name or _name(manufacturer),
            fieldset_name=_name(fieldset) if fieldset_id is not None else None,
        )

    def pick_manufacturer(self, current: Any = None) -> Any:
        query_text = (
            current if isinstance(current, str) else _name(current) if current is not None else None
        )
        while True:
            _page("Choose manufacturer", controls=_TEXT_HELP)
            query = _prompt("Search manufacturers", default=query_text)
            if query is BACK:
                return BACK
            query_text = str(query)
            try:
                results = self.service.search("manufacturers", query_text)
            except SnipeITAuthenticationError:
                raise
            except Exception as exc:
                message = _api_message(exc) if isinstance(exc, SnipeITException) else str(exc)
                console.print(f"[red]Error:[/red] {message}")
                _page(
                    "Choose manufacturer",
                    f"Search failed for {query_text}; retry or press Esc to cancel",
                    controls=_TEXT_HELP,
                    clear=False,
                )
                continue
            selected = _choose(
                "Matches",
                [_resource_label(item) for item in results] + ["Create a new manufacturer"],
                fuzzy=True,
            )
            if _is_back(selected):
                return BACK
            if selected == len(results):
                value = _prompt("Manufacturer name", default=query_text)
                if value is BACK:
                    continue
                return str(value)
            assert isinstance(selected, int)
            return results[selected]


def run_interactive() -> None:
    from .commands._common import get_client

    if state.json_output:
        raise typer.BadParameter("--json cannot be used with interactive mode.")
    if not _is_tty():
        raise typer.BadParameter("Interactive mode requires a terminal.")
    if state.config is None:
        target = _xdg_config_path()
        _page("Configuration", "No config.toml was found", controls=_CONFIRM_NO_BACK_HELP)
        create_config = _confirm(f"Create a starter config at {target}?", default=True, back=False)
        if create_config is True:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(DEFAULT_CONFIG_TEMPLATE)
            console.print(f"[green]✓[/green] Config written to: {target}")
        raise typer.Exit(1)
    if not state.api_key:
        console.print(
            "[red]Error:[/red] Set SNIPEIT_API_KEY or pass --api-key before using interactive mode."
        )
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
    details = [
        str(getattr(item, "model_number", "") or ""),
        _nested_name(getattr(item, "manufacturer", None)),
    ]
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
        ("Asset Tag", asset.asset_tag),
        ("Serial", asset.serial),
        ("Name", asset.name),
        ("Model", _nested_name(asset.model)),
        ("Status", _nested_name(getattr(asset, "status_label", None))),
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


def _print_update_review(
    asset: Asset,
    config: Any,
    values: dict[str, str],
) -> None:
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
        if key.startswith("new_model_"):
            label = dict(_NEW_MODEL_REVIEW_KEYS)[key]
            table.add_row(label, "(new model)", value)
        else:
            table.add_row(labels.get(key, key.replace("_", " ").title()), current[key], value)
    console.print(table)


def _new_model_review_values(model: NewModel) -> dict[str, str]:
    """Render all related records that a new-model update will create."""

    return {
        "new_model_manufacturer": model.manufacturer_display or "(existing)",
        "new_model_category": model.category_name or str(model.category_id),
        "new_model_fieldset": model.fieldset_name or "(none)",
        "new_model_number": model.model_number or "(blank)",
        "new_model_notes": model.notes or "(blank)",
    }
