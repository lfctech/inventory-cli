from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from snipeit import SnipeIT
from typer.testing import CliRunner

from inventory.main import app
from tests.e2e.helpers import id_int, parse_json_output, unique_name

pytestmark = pytest.mark.integration


def test_models_create_get_and_list_against_live_snipeit(
    runner: CliRunner,
    config_file,
    real_snipeit_client: SnipeIT,
    base,
    run_id: str,
) -> None:
    model_name = unique_name("model", run_id)
    model_id: int | None = None

    try:
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "models",
                "create",
                "--name",
                model_name,
                "--category",
                base["category"].name,
                "--manufacturer",
                base["manufacturer"].name,
                "--model-number",
                f"MODEL-{run_id}",
            ],
        )

        assert result.exit_code == 0, result.stderr
        created = parse_json_output(result.stdout)
        model_id = int(created["id"])
        assert created["name"] == model_name
        assert created["category"] == base["category"].name
        assert created["manufacturer"] == base["manufacturer"].name

        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "models",
                "get",
                "--name",
                model_name,
            ],
        )

        assert result.exit_code == 0, result.stderr
        fetched = parse_json_output(result.stdout)
        assert fetched["id"] == model_id
        assert fetched["name"] == model_name

        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "models",
                "list",
                "--search",
                model_name,
            ],
        )

        assert result.exit_code == 0, result.stderr
        listed = parse_json_output(result.stdout)
        assert any(row["id"] == model_id and row["name"] == model_name for row in listed)
    finally:
        if model_id is not None:
            with contextlib.suppress(Exception):
                real_snipeit_client.models.delete(model_id)


def test_assets_create_get_and_update_against_live_snipeit(
    runner: CliRunner,
    config_file,
    real_snipeit_client: SnipeIT,
    base,
    run_id: str,
) -> None:
    asset_name = unique_name("asset", run_id)
    asset_tag = unique_name("tag", run_id)
    serial = unique_name("serial", run_id)
    asset_id: int | None = None

    try:
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "assets",
                "create",
                "--model",
                base["model"].name,
                "--status",
                base["status"].name,
                "--asset-tag",
                asset_tag,
                "--serial",
                serial,
                "--name",
                asset_name,
            ],
        )

        assert result.exit_code == 0, result.stderr
        created = parse_json_output(result.stdout)
        asset_id = int(created["id"])
        assert created["asset_tag"] == asset_tag
        assert created["name"] == asset_name
        assert created["serial"] == serial

        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "assets",
                "get",
                "--id",
                str(asset_id),
            ],
        )

        assert result.exit_code == 0, result.stderr
        fetched = parse_json_output(result.stdout)
        assert fetched["id"] == asset_id
        assert fetched["asset_tag"] == asset_tag
        assert fetched["serial"] == serial

        updated_name = unique_name("asset-updated", run_id)
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "assets",
                "update",
                "--id",
                str(asset_id),
                "--name",
                updated_name,
            ],
        )

        assert result.exit_code == 0, result.stderr
        updated = parse_json_output(result.stdout)
        assert updated["id"] == asset_id
        assert updated["name"] == updated_name

        live_asset = real_snipeit_client.assets.get(asset_id)
        assert id_int(live_asset) == asset_id
        assert live_asset.name == updated_name
    finally:
        if asset_id is not None:
            with contextlib.suppress(Exception):
                real_snipeit_client.assets.delete(asset_id)


def test_assets_files_upload_list_download_delete_against_live_snipeit(
    runner: CliRunner,
    config_file,
    real_snipeit_client: SnipeIT,
    base,
    run_id: str,
    tmp_path: Path,
) -> None:
    asset_tag = unique_name("files-tag", run_id)
    serial = unique_name("files-serial", run_id)
    asset_name = unique_name("files-asset", run_id)
    asset_id: int | None = None

    # A small text attachment we can round-trip and byte-compare.
    upload_src = tmp_path / "attachment.txt"
    file_body = f"inventory-e2e attachment {run_id}\n"
    upload_src.write_text(file_body)

    try:
        # Create the asset that the files will hang off of.
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "assets",
                "create",
                "--model",
                base["model"].name,
                "--status",
                base["status"].name,
                "--asset-tag",
                asset_tag,
                "--serial",
                serial,
                "--name",
                asset_name,
            ],
        )
        assert result.exit_code == 0, result.stderr
        asset_id = int(parse_json_output(result.stdout)["id"])

        # Upload the file (also exercises --notes).
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "assets",
                "files",
                "upload",
                "--id",
                str(asset_id),
                "--notes",
                "inventory-e2e upload",
                str(upload_src),
            ],
        )
        assert result.exit_code == 0, result.stderr

        # List files and recover the uploaded file's ID.
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "assets",
                "files",
                "list",
                "--id",
                str(asset_id),
            ],
        )
        assert result.exit_code == 0, result.stderr
        listing = parse_json_output(result.stdout)
        rows = listing["rows"] if isinstance(listing, dict) else listing
        assert len(rows) >= 1, f"expected at least one file, got {listing!r}"
        file_id = int(rows[0]["id"])

        # Download to a controlled path and verify the bytes round-trip.
        dest = tmp_path / "downloaded.txt"
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "assets",
                "files",
                "download",
                "--id",
                str(asset_id),
                "--file-id",
                str(file_id),
                "--output",
                str(dest),
            ],
        )
        assert result.exit_code == 0, result.stderr
        downloaded = parse_json_output(result.stdout)
        assert downloaded["status"] == "success"
        assert dest.exists()
        assert dest.read_text() == file_body

        # Delete the file (--force skips the confirmation prompt).
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "assets",
                "files",
                "delete",
                "--id",
                str(asset_id),
                "--file-id",
                str(file_id),
                "--force",
            ],
        )
        assert result.exit_code == 0, result.stderr
        assert parse_json_output(result.stdout)["status"] == "success"

        # Confirm the file is gone.
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_file),
                "--json",
                "assets",
                "files",
                "list",
                "--id",
                str(asset_id),
            ],
        )
        assert result.exit_code == 0, result.stderr
        listing = parse_json_output(result.stdout)
        remaining = listing["rows"] if isinstance(listing, dict) else listing
        assert all(int(row["id"]) != file_id for row in remaining)
    finally:
        if asset_id is not None:
            with contextlib.suppress(Exception):
                real_snipeit_client.assets.delete(asset_id)
