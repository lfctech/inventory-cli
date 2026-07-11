"""UI-neutral application services shared by CLI and interactive workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from snipeit import SnipeIT
from snipeit.exceptions import SnipeITException, SnipeITNotFoundError
from snipeit.resources.assets import Asset
from snipeit.resources.models import Model

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


@dataclass
class CreatedResources:
    """Records created during a transaction and eligible for rollback."""

    model_id: int | None = None
    manufacturer_id: int | None = None
    rollback_errors: list[str] = field(default_factory=list)


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
        try:
            if new_model is not None:
                model_id = self._create_model(new_model, created)
            payload: dict[str, Any] = {"model_id": model_id, "status_id": status_id}
            if serial:
                payload["serial"] = serial
            asset = self.client.assets.create(**payload)
            if asset.id is None:
                raise RuntimeError("Asset was created but the server returned no ID.")
            return asset, created
        except Exception:
            self.rollback(created)
            raise

    def create_model(self, spec: NewModel) -> tuple[Model, CreatedResources]:
        created = CreatedResources()
        try:
            model_id = self._create_model(spec, created)
            return self.client.models.get(model_id), created
        except Exception:
            self.rollback(created)
            raise

    def _create_model(self, spec: NewModel, created: CreatedResources) -> int:
        manufacturer_id = spec.manufacturer_id
        if manufacturer_id is None:
            if not spec.manufacturer_name:
                raise ValueError("A manufacturer selection or new manufacturer name is required.")
            manufacturer = self.client.manufacturers.create(name=spec.manufacturer_name)
            if manufacturer.id is None:
                raise RuntimeError("Manufacturer was created but the server returned no ID.")
            manufacturer_id = int(manufacturer.id)
            created.manufacturer_id = manufacturer_id

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
        model = self.client.models.create(**payload)
        if model.id is None:
            raise RuntimeError("Model was created but the server returned no ID.")
        created.model_id = int(model.id)
        return int(model.id)

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

    def save_asset(self, asset: Asset) -> Asset:
        asset.save()
        try:
            asset.refresh()
        except SnipeITException:
            # The write already succeeded. Keep the locally updated object rather
            # than reporting failure or rolling back resources it may now use.
            pass
        return asset

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
                    asset.set_custom_field(label, changes[key])
            asset.save()
        except Exception:
            self.rollback(created)
            raise
        try:
            asset.refresh()
        except SnipeITException:
            pass
        return asset, created

    def save_label(self, asset: Asset, output: str | Path) -> str:
        if not asset.asset_tag:
            raise ValueError("Asset has no asset tag — cannot generate label.")
        return str(self.client.assets.labels(str(output), [asset.asset_tag]))
