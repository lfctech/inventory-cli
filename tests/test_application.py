"""Unit tests for UI-neutral application services."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from snipeit.exceptions import (
    SnipeITApiError,
    SnipeITNotFoundError,
    SnipeITTimeoutError,
    SnipeITValidationError,
)
from snipeit.resources.assets import Asset

from inventory.application import InventoryService, MutationOutcome, NewModel, TransactionError

pytestmark = pytest.mark.unit


def _not_found() -> SnipeITNotFoundError:
    return SnipeITNotFoundError("missing")


def test_find_asset_queries_tag_and_serial_and_deduplicates() -> None:
    asset = SimpleNamespace(id=7)
    client = Mock()
    client.assets.get_by_tag.return_value = asset
    client.assets.get_by_serial.return_value = asset

    matches = InventoryService(client).find_asset("ABC")

    client.assets.get_by_tag.assert_called_once_with("ABC")
    client.assets.get_by_serial.assert_called_once_with("ABC")
    assert matches.unique == [asset]


def test_find_asset_only_suppresses_not_found() -> None:
    serial_asset = SimpleNamespace(id=8)
    client = Mock()
    client.assets.get_by_tag.side_effect = _not_found()
    client.assets.get_by_serial.return_value = serial_asset

    assert InventoryService(client).find_asset("SERIAL").unique == [serial_asset]

    client.assets.get_by_tag.side_effect = SnipeITValidationError("bad request")
    with pytest.raises(SnipeITValidationError):
        InventoryService(client).find_asset("SERIAL")


def test_find_asset_keeps_valid_tag_when_serial_is_duplicated() -> None:
    tag_asset = SimpleNamespace(id=8)
    client = Mock()
    client.assets.get_by_tag.return_value = tag_asset
    client.assets.get_by_serial.side_effect = SnipeITApiError("Expected 1 asset, found 2")

    assert InventoryService(client).find_asset("DUPLICATE").unique == [tag_asset]


def test_create_asset_rolls_back_new_model_and_manufacturer_on_failure() -> None:
    client = Mock()
    client.manufacturers.create.return_value = SimpleNamespace(id=2)
    client.models.create.return_value = SimpleNamespace(id=3)
    client.assets.create.side_effect = SnipeITValidationError("invalid asset")
    spec = NewModel(name="Model", category_id=1, manufacturer_name="New Mfg")

    with pytest.raises(TransactionError) as raised:
        InventoryService(client).create_asset(status_id=4, serial="SN", new_model=spec)

    assert isinstance(raised.value.cause, SnipeITValidationError)
    client.models.delete.assert_called_once_with(3)
    client.manufacturers.delete.assert_called_once_with(2)


def test_create_asset_does_not_delete_preexisting_records_on_failure() -> None:
    client = Mock()
    client.assets.create.side_effect = SnipeITValidationError("invalid asset")

    with pytest.raises(TransactionError):
        InventoryService(client).create_asset(status_id=4, serial="SN", model_id=9)

    client.models.delete.assert_not_called()
    client.manufacturers.delete.assert_not_called()


def test_save_label_requires_asset_tag() -> None:
    client = Mock()
    asset = cast(Asset, SimpleNamespace(asset_tag=None))
    with pytest.raises(ValueError, match="no asset tag"):
        InventoryService(client).save_label(asset, "label.pdf")


def test_update_asset_rolls_back_new_model_when_save_fails(config_file) -> None:
    from inventory.config import load_config

    client = Mock()
    client.models.create.return_value = SimpleNamespace(id=3)
    asset = Mock()
    asset.save.side_effect = SnipeITValidationError("invalid update")
    spec = NewModel(name="Model", category_id=1, manufacturer_id=2)

    with pytest.raises(TransactionError):
        InventoryService(client).update_asset(
            asset,
            load_config(config_file),
            {"name": "Changed"},
            new_model=spec,
        )

    client.models.delete.assert_called_once_with(3)
    client.manufacturers.delete.assert_not_called()


def test_transaction_error_preserves_rollback_failures() -> None:
    client = Mock()
    client.manufacturers.create.return_value = SimpleNamespace(id=2)
    client.models.create.return_value = SimpleNamespace(id=3)
    client.assets.create.side_effect = SnipeITValidationError("invalid asset")
    client.models.delete.side_effect = SnipeITValidationError("model in use")
    spec = NewModel(name="Model", category_id=1, manufacturer_name="New Mfg")

    with pytest.raises(TransactionError) as raised:
        InventoryService(client).create_asset(status_id=4, serial="SN", new_model=spec)

    assert raised.value.rollback_errors
    assert "Could not delete model 3" in raised.value.rollback_errors[0]


def test_update_does_not_roll_back_when_refresh_fails_after_save(config_file) -> None:
    from inventory.config import load_config

    client = Mock()
    client.models.create.return_value = SimpleNamespace(id=3)
    asset = Mock()
    asset.refresh.side_effect = SnipeITValidationError("refresh failed")
    spec = NewModel(name="Model", category_id=1, manufacturer_id=2)

    updated, created = InventoryService(client).update_asset(
        asset,
        load_config(config_file),
        {"name": "Changed"},
        new_model=spec,
    )

    assert updated is asset
    assert created.outcome is MutationOutcome.COMPLETED
    assert not created.refresh_verified
    assert created.refresh_error == "refresh failed"
    asset.save.assert_called_once_with()
    client.models.delete.assert_not_called()


def test_create_timeout_is_ambiguous_and_does_not_delete_related_records() -> None:
    client = Mock()
    client.manufacturers.create.return_value = SimpleNamespace(id=2)
    client.models.create.return_value = SimpleNamespace(id=3)
    client.assets.create.side_effect = SnipeITTimeoutError("timed out")
    spec = NewModel(name="Model", category_id=1, manufacturer_name="New Mfg")

    with pytest.raises(TransactionError) as raised:
        InventoryService(client).create_asset(status_id=4, serial="SN", new_model=spec)

    assert raised.value.outcome is MutationOutcome.AMBIGUOUS
    assert raised.value.created is not None
    assert raised.value.created.model_id == 3
    client.models.delete.assert_not_called()
    client.manufacturers.delete.assert_not_called()


def test_ctrl_c_during_model_creation_is_ambiguous_without_unsafe_cleanup() -> None:
    client = Mock()
    client.manufacturers.create.return_value = SimpleNamespace(id=2)
    client.models.create.side_effect = KeyboardInterrupt
    spec = NewModel(name="Model", category_id=1, manufacturer_name="New Mfg")

    with pytest.raises(TransactionError) as raised:
        InventoryService(client).create_asset(status_id=4, serial="SN", new_model=spec)

    assert raised.value.outcome is MutationOutcome.AMBIGUOUS
    client.models.delete.assert_not_called()
    client.manufacturers.delete.assert_not_called()


def test_missing_model_id_is_ambiguous_without_deleting_manufacturer() -> None:
    client = Mock()
    client.manufacturers.create.return_value = SimpleNamespace(id=2)
    client.models.create.return_value = SimpleNamespace(id=None)
    spec = NewModel(name="Model", category_id=1, manufacturer_name="New Mfg")

    with pytest.raises(TransactionError) as raised:
        InventoryService(client).create_asset(status_id=4, serial="SN", new_model=spec)

    assert raised.value.outcome is MutationOutcome.AMBIGUOUS
    client.manufacturers.delete.assert_not_called()


def test_update_rejects_custom_field_missing_from_asset_fieldset(config_file) -> None:
    from inventory.config import load_config

    client = Mock()
    client.models.create.return_value = SimpleNamespace(id=3)
    asset = Mock()
    asset.custom_fields = {"CPU": {"field": "_snipeit_cpu_1", "value": ""}}

    with pytest.raises(ValueError, match="not available"):
        InventoryService(client).update_asset(
            asset,
            load_config(config_file),
            {"ram": 16},
        )

    asset.save.assert_not_called()


def test_update_rejects_model_and_custom_field_in_one_mutation(config_file) -> None:
    from inventory.config import load_config

    client = Mock()
    asset = Mock()
    asset.custom_fields = {"RAM (GB)": {"field": "_snipeit_ram_gb_3", "value": ""}}
    spec = NewModel(name="Model", category_id=1, manufacturer_id=2)

    with pytest.raises(ValueError, match="Save the model change"):
        InventoryService(client).update_asset(
            asset,
            load_config(config_file),
            {"ram": 16},
            new_model=spec,
        )

    client.models.create.assert_not_called()
    asset.save.assert_not_called()


def test_update_timeout_is_ambiguous_and_preserves_asset_id(config_file) -> None:
    from inventory.config import load_config

    client = Mock()
    client.models.create.return_value = SimpleNamespace(id=3)
    asset = Mock()
    asset.id = 42
    asset.save.side_effect = SnipeITTimeoutError("timed out")
    spec = NewModel(name="Model", category_id=1, manufacturer_id=2)

    with pytest.raises(TransactionError) as raised:
        InventoryService(client).update_asset(
            asset,
            load_config(config_file),
            {"name": "Changed"},
            new_model=spec,
        )

    assert raised.value.outcome is MutationOutcome.AMBIGUOUS
    assert raised.value.asset_id == 42
    client.models.delete.assert_not_called()
