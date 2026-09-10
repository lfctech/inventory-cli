"""UI-neutral application services shared by CLI and interactive workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from snipeit import SnipeIT
from snipeit.exceptions import (
    SnipeITApiError,
    SnipeITException,
    SnipeITNotFoundError,
    SnipeITServerError,
    SnipeITTimeoutError,
)
from snipeit.resources.assets import Asset

from .config import AppConfig


@dataclass(frozen=True)
class AssetMatches:
    """Results from treating one identifier as both a tag and a serial."""

    tag: Asset | None = None
    serial: Asset | None = None

    @property
    def unique(self) -> list[Asset]:
        matches: list[Asset] = []
        seen: set[str] = set()
        for asset in (self.tag, self.serial):
            if asset is None:
                continue
            key = str(asset.id)
            if key not in seen:
                seen.add(key)
                matches.append(asset)
        return matches


@dataclass(frozen=True)
class NewModel:
    name: str
    category_id: int
    manufacturer_id: int | None = None
    manufacturer_name: str | None = None
    fieldset_id: int | None = None
    model_number: str | None = None
    notes: str | None = None
    category_name: str | None = None
    manufacturer_display: str | None = None
    fieldset_name: str | None = None


class MutationOutcome(StrEnum):
    """Outcome classification for a mutating API workflow."""

    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    AMBIGUOUS = "ambiguous"


@dataclass
class CreatedResources:
    """Records created during a transaction and eligible for rollback."""

    model_id: int | None = None
    manufacturer_id: int | None = None
    rollback_errors: list[str] = field(default_factory=list)
    outcome: MutationOutcome = MutationOutcome.COMPLETED
    refresh_verified: bool = True
    refresh_error: str | None = None
    in_flight: str | None = None
    mutation_confirmed: bool = False


class TransactionError(RuntimeError):
    """An operation failed after creating records that required rollback."""

    def __init__(
        self,
        cause: BaseException,
        rollback_errors: list[str],
        *,
        outcome: MutationOutcome,
        asset_id: int | str | None = None,
        created: CreatedResources | None = None,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.rollback_errors = rollback_errors
        self.outcome = outcome
        self.asset_id = asset_id
        self.created = created


class InventoryService:
    """Application operations without prompting, printing, or process exits."""

    def __init__(self, client: SnipeIT) -> None:
        self.client = client

    def resolve_asset(
        self,
        *,
        asset_id: int | None = None,
        tag: str | None = None,
        serial: str | None = None,
    ) -> Asset:
        supplied = sum(value is not None for value in (asset_id, tag, serial))
        if supplied == 0:
            raise ValueError("Provide one of --id, --tag, or --serial.")
        if supplied > 1:
            raise ValueError("Provide only one of --id, --tag, or --serial.")
        if asset_id is not None:
            return self.client.assets.get(asset_id)
        if tag is not None:
            return self.client.assets.get_by_tag(tag)
        assert serial is not None
        return self.client.assets.get_by_serial(serial)

    def find_asset(self, identifier: str) -> AssetMatches:
        """Perform exact tag and serial lookups; only absence is suppressed."""
        try:
            tag_match = self.client.assets.get_by_tag(identifier)
        except SnipeITNotFoundError:
            tag_match = None
        try:
            serial_match = self.client.assets.get_by_serial(identifier)
        except SnipeITNotFoundError:
            serial_match = None
        except SnipeITApiError as exc:
            # A bare API error is how the client reports duplicate serials. A
            # unique tag remains usable; typed auth/server/timeout errors keep
            # propagating so they are never misreported as absence.
            if tag_match is None or type(exc) is not SnipeITApiError:
                raise
            serial_match = None
        return AssetMatches(tag=tag_match, serial=serial_match)

    def search(self, resource: str, query: str | None = None, *, limit: int = 20) -> list[Any]:
        endpoint = getattr(self.client, resource)
        return list(endpoint.list_all(search=query, limit=limit))

    def create_asset(
        self,
        *,
        status_id: int,
        serial: str | None,
        model_id: int | None = None,
        new_model: NewModel | None = None,
    ) -> tuple[Asset, CreatedResources]:
        """Create an asset and roll back records created by this call on failure."""
        if (model_id is None) == (new_model is None):
            raise ValueError("Provide exactly one of model_id or new_model.")
        created = CreatedResources()
        asset: Asset | None = None
        try:
            if new_model is not None:
                model_id = self._create_model(new_model, created)
            payload: dict[str, Any] = {"model_id": model_id, "status_id": status_id}
            if serial:
                payload["serial"] = serial
            created.in_flight = "asset"
            asset = self.client.assets.create(**payload)
            created.mutation_confirmed = True
            created.in_flight = None
            if asset.id is None:
                raise _AmbiguousMutationError(
                    "Asset may have been created, but the server returned no ID."
                )
            created.outcome = MutationOutcome.COMPLETED
            return asset, created
        except BaseException as exc:
            outcome = _mutation_outcome(exc, created)
            created.outcome = outcome
            if outcome is MutationOutcome.ROLLED_BACK:
                self.rollback(created)
                if created.rollback_errors:
                    created.outcome = MutationOutcome.AMBIGUOUS
                    outcome = created.outcome
            raise TransactionError(
                exc,
                created.rollback_errors,
                outcome=outcome,
                asset_id=asset.id if asset is not None else None,
                created=created,
            ) from exc

    def _create_model(self, spec: NewModel, created: CreatedResources) -> int:
        manufacturer_id = spec.manufacturer_id
        if manufacturer_id is None:
            if not spec.manufacturer_name:
                raise ValueError("A manufacturer selection or new manufacturer name is required.")
            created.in_flight = "manufacturer"
            manufacturer = self.client.manufacturers.create(name=spec.manufacturer_name)
            if manufacturer.id is None:
                raise _AmbiguousMutationError(
                    "Manufacturer may have been created, but the server returned no ID."
                )
            try:
                manufacturer_id = int(manufacturer.id)
            except (TypeError, ValueError) as exc:
                raise _AmbiguousMutationError(
                    "Manufacturer may have been created, but its ID was invalid."
                ) from exc
            created.manufacturer_id = manufacturer_id
            created.in_flight = None

        payload: dict[str, Any] = {
            "name": spec.name,
            "category_id": spec.category_id,
            "manufacturer_id": manufacturer_id,
        }
        if spec.fieldset_id is not None:
            payload["fieldset_id"] = spec.fieldset_id
        if spec.model_number:
            payload["model_number"] = spec.model_number
        if spec.notes:
            payload["notes"] = spec.notes
        created.in_flight = "model"
        model = self.client.models.create(**payload)
        if model.id is None:
            raise _AmbiguousMutationError(
                "Model may have been created, but the server returned no ID."
            )
        try:
            created.model_id = int(model.id)
        except (TypeError, ValueError) as exc:
            raise _AmbiguousMutationError(
                "Model may have been created, but its ID was invalid."
            ) from exc
        created.in_flight = None
        assert created.model_id is not None
        return created.model_id

    def rollback(self, created: CreatedResources) -> None:
        for resource, record_id in (
            ("models", created.model_id),
            ("manufacturers", created.manufacturer_id),
        ):
            if record_id is None:
                continue
            try:
                getattr(self.client, resource).delete(record_id)
            except SnipeITException as exc:
                created.rollback_errors.append(f"Could not delete {resource[:-1]} {record_id}: {exc}")

    def update_asset(
        self,
        asset: Asset,
        config: AppConfig,
        changes: dict[str, Any],
        *,
        model_id: int | None = None,
        new_model: NewModel | None = None,
    ) -> tuple[Asset, CreatedResources]:
        """Apply interactive edits, rolling back a newly-created model on failure."""
        self._validate_update_fields(asset, config, changes, model_id=model_id, new_model=new_model)
        created = CreatedResources()
        try:
            if new_model is not None:
                model_id = self._create_model(new_model, created)
            if model_id is not None:
                asset.model_id = model_id
            if "status_id" in changes:
                asset.status_id = changes["status_id"]
            if "name" in changes:
                asset.name = changes["name"]

            labels = {
                "cpu": config.custom_fields.cpu_model,
                "ram": config.custom_fields.ram,
                "storage": config.custom_fields.storage,
                "touch_screen": config.custom_fields.touch_screen,
                "passmark": config.custom_fields.cpu_passmark,
                "sale_price": config.custom_fields.sale_price,
            }
            for key, label in labels.items():
                if key in changes:
                    value = changes[key]
                    asset.set_custom_field(label, "" if value == "" else str(value))
            created.in_flight = "asset"
            asset.save()
            created.mutation_confirmed = True
            created.in_flight = None
        except BaseException as exc:
            outcome = _mutation_outcome(exc, created)
            created.outcome = outcome
            if outcome is MutationOutcome.ROLLED_BACK:
                self.rollback(created)
                if created.rollback_errors:
                    created.outcome = MutationOutcome.AMBIGUOUS
                    outcome = created.outcome
            raise TransactionError(
                exc,
                created.rollback_errors,
                outcome=outcome,
                asset_id=asset.id,
                created=created,
            ) from exc
        try:
            asset.refresh()
        except SnipeITException as exc:
            created.refresh_verified = False
            created.refresh_error = str(exc)
        return asset, created

    @staticmethod
    def _validate_update_fields(
        asset: Asset,
        config: AppConfig,
        changes: dict[str, Any],
        *,
        model_id: int | None,
        new_model: NewModel | None,
    ) -> None:
        custom_labels = {
            "cpu": config.custom_fields.cpu_model,
            "ram": config.custom_fields.ram,
            "storage": config.custom_fields.storage,
            "touch_screen": config.custom_fields.touch_screen,
            "passmark": config.custom_fields.cpu_passmark,
            "sale_price": config.custom_fields.sale_price,
        }
        custom_changes = set(changes) & set(custom_labels)
        if custom_changes and (model_id is not None or new_model is not None):
            raise ValueError(
                "Save the model change before editing custom fields so the target fieldset is known."
            )
        custom_fields = getattr(asset, "custom_fields", None)
        if not isinstance(custom_fields, dict):
            if custom_changes:
                raise ValueError(
                    "This asset's custom-field fieldset is unavailable; refresh it before editing."
                )
            return
        for key in custom_changes:
            label = custom_labels[key]
            if label not in custom_fields:
                raise ValueError(
                    f"Custom field {label!r} is not available on this asset's model fieldset."
                )

    def save_label(self, asset: Asset, output: str | Path) -> str:
        if not asset.asset_tag:
            raise ValueError("Asset has no asset tag — cannot generate label.")
        return str(self.client.assets.labels(str(output), [asset.asset_tag]))


class _AmbiguousMutationError(RuntimeError):
    """Internal marker for a response that cannot prove a write failed."""


def _mutation_outcome(exc: BaseException, created: CreatedResources) -> MutationOutcome:
    """Classify a failed mutation before deciding whether cleanup is safe."""

    if isinstance(exc, _AmbiguousMutationError):
        return MutationOutcome.AMBIGUOUS
    if created.mutation_confirmed:
        return MutationOutcome.AMBIGUOUS
    if isinstance(exc, KeyboardInterrupt):
        return (
            MutationOutcome.AMBIGUOUS
            if created.in_flight is not None
            else MutationOutcome.ROLLED_BACK
        )
    if isinstance(exc, (SnipeITTimeoutError, SnipeITServerError)):
        return MutationOutcome.AMBIGUOUS
    if isinstance(exc, SnipeITApiError):
        # A typed 4xx response, or a 2xx response carrying an explicit API
        # error, proves that the mutation was rejected. A 5xx is handled
        # above because the server may have committed before failing.
        status = exc.status_code
        if status is None or status < 500:
            return MutationOutcome.ROLLED_BACK
        return MutationOutcome.AMBIGUOUS
    if created.in_flight is not None:
        return MutationOutcome.AMBIGUOUS
    if isinstance(exc, (RuntimeError, ValueError)):
        return MutationOutcome.ROLLED_BACK
    return MutationOutcome.ROLLED_BACK
