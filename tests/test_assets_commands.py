"""Tier 2: CLI command tests for ``inventory assets``.

We exercise the typer command surface end-to-end with ``CliRunner``, but
mock the Snipe-IT HTTP layer via pytest-httpx. This catches integration
regressions between the CLI flow and the snipeit-python-api package
without standing up a real Snipe-IT.
"""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from inventory.main import app

from .conftest import TEST_URL, asset_payload, strip_ansi

pytestmark = pytest.mark.unit


# ── Lookup-flag validation ───────────────────────────────────────────────────


def test_get_requires_a_lookup_flag(
    runner: CliRunner, fake_env, reset_state, config_file
) -> None:
    result = runner.invoke(app, ["--config", str(config_file), "assets", "get"])
    assert result.exit_code == 1
    assert "Provide one of --id, --tag, or --serial" in result.stderr


def test_get_rejects_multiple_lookup_flags(
    runner: CliRunner, fake_env, reset_state, config_file
) -> None:
    result = runner.invoke(
        app,
        [
            "--config", str(config_file),
            "assets", "get",
            "--id", "1",
            "--tag", "LFC-1",
        ],
    )
    assert result.exit_code == 1
    assert "Provide only one of --id, --tag, or --serial" in result.stderr


# ── assets get ───────────────────────────────────────────────────────────────


def test_get_by_id_success_table_output(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json=asset_payload(asset_id=1, asset_tag="LFC-1042", name="Demo Laptop"),
    )
    result = runner.invoke(
        app,
        ["--config", str(config_file), "assets", "get", "--id", "1"],
    )
    assert result.exit_code == 0, result.stderr
    # Table output renders to stdout — assert key fields are present.
    assert "LFC-1042" in result.stdout
    assert "Demo Laptop" in result.stdout
    assert "Refurbished" in result.stdout  # status_label.name from default payload


def test_get_by_tag_uses_bytag_endpoint(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/bytag/LFC-99",
        json=asset_payload(asset_id=99, asset_tag="LFC-99"),
    )
    result = runner.invoke(
        app,
        ["--config", str(config_file), "assets", "get", "--tag", "LFC-99"],
    )
    assert result.exit_code == 0, result.stderr
    assert "LFC-99" in result.stdout


def test_get_json_output_machine_readable(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json=asset_payload(
            asset_id=1,
            asset_tag="LFC-1",
            name="JSON Asset",
            custom_fields={
                "CPU": {"field": "_snipeit_cpu_1", "value": "i5-10400"},
                "CPU PassMark Score": {"field": "_snipeit_cpu_passmark_score_2", "value": "8000"},
                "RAM (GB)": {"field": "_snipeit_ram_gb_3", "value": "16"},
                "Storage (GB)": {"field": "_snipeit_storage_gb_4", "value": "512"},
                "Sale Price": {"field": "_snipeit_sale_price_5", "value": "175"},
                "Touchscreen": {"field": "_snipeit_touchscreen_6", "value": "0"},
            },
        ),
    )
    result = runner.invoke(
        app,
        ["--config", str(config_file), "--json", "assets", "get", "--id", "1"],
    )
    assert result.exit_code == 0, result.stderr
    payload = json.loads(strip_ansi(result.stdout))
    assert payload["id"] == 1
    assert payload["asset_tag"] == "LFC-1"
    assert payload["cpu"] == "i5-10400"
    assert payload["passmark"] == "8000"
    assert payload["ram"] == "16"
    assert payload["sale_price"] == "175"


def test_get_not_found_surfaces_friendly_error(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/9999",
        status_code=404,
        json={"status": "error", "messages": "Asset not found"},
    )
    result = runner.invoke(
        app,
        ["--config", str(config_file), "assets", "get", "--id", "9999"],
    )
    assert result.exit_code == 1
    assert "Asset not found" in result.stderr


# ── assets create ────────────────────────────────────────────────────────────


def test_create_basic_no_custom_fields(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    # Resolvers query list endpoints first.
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/models\b"),
        json={"total": 1, "rows": [{"id": 5, "name": "Latitude 5440"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/statuslabels\b"),
        json={"total": 1, "rows": [{"id": 9, "name": "Ready"}]},
    )
    # POST /hardware returns the created asset (envelope shape).
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_URL}/api/v1/hardware",
        json={"status": "success", "payload": asset_payload(asset_id=42, asset_tag="LFC-42")},
    )

    result = runner.invoke(
        app,
        [
            "--config", str(config_file),
            "assets", "create",
            "--model", "Latitude 5440",
            "--status", "Ready",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert "Asset created" in result.stderr
    assert "LFC-42" in result.stdout

    # Verify the POST body contained the resolved IDs.
    posts = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert len(posts) == 1
    body = json.loads(posts[0].content)
    assert body == {"status_id": 9, "model_id": 5}


def test_create_with_custom_fields_full_flow(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    """Create + refresh + set_custom_field + save end-to-end.

    Mirrors the v0.4.0 staging contract: the POST returns an asset with
    null/empty custom_fields; refresh() pulls the populated read shape;
    save() PATCHes the staged column-name keys.
    """
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/models\b"),
        json={"total": 1, "rows": [{"id": 5, "name": "Latitude 5440"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/statuslabels\b"),
        json={"total": 1, "rows": [{"id": 9, "name": "Ready"}]},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_URL}/api/v1/hardware",
        json={"status": "success", "payload": {"id": 100, "asset_tag": "LFC-100"}},
    )
    # asset.refresh() — fetches /hardware/100 and populates custom_fields.
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/100",
        json=asset_payload(asset_id=100, asset_tag="LFC-100"),
    )
    # asset.save() PATCHes the staged custom field columns.
    httpx_mock.add_response(
        method="PATCH",
        url=f"{TEST_URL}/api/v1/hardware/100",
        json={"status": "success", "payload": asset_payload(asset_id=100, asset_tag="LFC-100")},
    )

    result = runner.invoke(
        app,
        [
            "--config", str(config_file),
            "assets", "create",
            "--model", "Latitude 5440",
            "--status", "Ready",
            "--cpu", "i5-10400",
            "--ram", "16",
            "--storage", "512",
            "--touch-screen",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert "LFC-100" in result.stdout

    patches = [r for r in httpx_mock.get_requests() if r.method == "PATCH"]
    assert len(patches) == 1
    body = json.loads(patches[0].content)
    # Custom-field PATCH uses the underlying _snipeit_<col> keys.
    assert body["_snipeit_cpu_1"] == "i5-10400"
    assert body["_snipeit_ram_gb_3"] == "16"
    assert body["_snipeit_storage_gb_4"] == "512"
    assert body["_snipeit_touchscreen_6"] == "1"


def test_create_partial_success_when_save_fails(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    """If create succeeds but the post-create save fails, the CLI tells
    the user the asset exists and how to recover — does not crash."""
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/models\b"),
        json={"total": 1, "rows": [{"id": 5, "name": "Latitude 5440"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/statuslabels\b"),
        json={"total": 1, "rows": [{"id": 9, "name": "Ready"}]},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_URL}/api/v1/hardware",
        json={"status": "success", "payload": {"id": 200, "asset_tag": "LFC-200"}},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/200",
        json=asset_payload(asset_id=200, asset_tag="LFC-200"),
    )
    # PATCH fails with a server error.
    httpx_mock.add_response(
        method="PATCH",
        url=f"{TEST_URL}/api/v1/hardware/200",
        status_code=500,
        json={"status": "error", "messages": "boom"},
    )

    result = runner.invoke(
        app,
        [
            "--config", str(config_file),
            "assets", "create",
            "--model", "Latitude 5440",
            "--status", "Ready",
            "--cpu", "i5-10400",
        ],
    )
    assert result.exit_code == 1
    # Rich may colour-style numbers/markup, breaking literal substring matches;
    # strip ANSI so we assert against the human-visible content.
    assert "inventory assets update --id 200" in strip_ansi(result.stderr)


# ── assets update ────────────────────────────────────────────────────────────


def test_update_no_changes_warns_and_exits_zero(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json=asset_payload(asset_id=1),
    )
    result = runner.invoke(
        app,
        ["--config", str(config_file), "assets", "update", "--id", "1"],
    )
    assert result.exit_code == 0, result.stderr
    assert "No fields to update" in result.stderr


def test_update_name_and_custom_field_patches_both(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json=asset_payload(asset_id=1, name="Old Name"),
    )
    httpx_mock.add_response(
        method="PATCH",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json={"status": "success", "payload": asset_payload(asset_id=1, name="New Name")},
    )

    result = runner.invoke(
        app,
        [
            "--config", str(config_file),
            "assets", "update",
            "--id", "1",
            "--name", "New Name",
            "--ram", "32",
        ],
    )
    assert result.exit_code == 0, result.stderr

    patches = [r for r in httpx_mock.get_requests() if r.method == "PATCH"]
    assert len(patches) == 1
    body = json.loads(patches[0].content)
    assert body["name"] == "New Name"
    assert body["_snipeit_ram_gb_3"] == "32"


# ── assets price ─────────────────────────────────────────────────────────────


def test_price_uses_existing_passmark_in_inventory(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    """Source 2 of the PassMark resolution chain: existing value in Snipe-IT."""
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json=asset_payload(
            asset_id=1,
            asset_tag="LFC-1",
            custom_fields={
                "CPU": {"field": "_snipeit_cpu_1", "value": "i5-10400"},
                "CPU PassMark Score": {"field": "_snipeit_cpu_passmark_score_2", "value": "8000"},
                "RAM (GB)": {"field": "_snipeit_ram_gb_3", "value": "16"},
                "Storage (GB)": {"field": "_snipeit_storage_gb_4", "value": "512"},
                "Sale Price": {"field": "_snipeit_sale_price_5", "value": ""},
                "Touchscreen": {"field": "_snipeit_touchscreen_6", "value": "0"},
            },
        ),
    )
    httpx_mock.add_response(
        method="PATCH",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json={"status": "success", "payload": asset_payload(asset_id=1)},
    )
    result = runner.invoke(
        app,
        ["--config", str(config_file), "assets", "price", "--id", "1"],
    )
    assert result.exit_code == 0, result.stderr
    assert "$175" in result.stdout  # 8 + 3 + 4 = 15 → tier $175

    patches = [r for r in httpx_mock.get_requests() if r.method == "PATCH"]
    assert len(patches) == 1
    body = json.loads(patches[0].content)
    # Price must round-trip back into Snipe-IT under the expected column.
    assert body["_snipeit_sale_price_5"] == "175"
    # The PassMark value (8000) already matches what's on the server, so
    # snipeit-python-api v0.4.0's set_custom_field cancels the noop stage —
    # only sale_price ends up in the PATCH body.
    assert "_snipeit_cpu_passmark_score_2" not in body


def test_price_dry_run_does_not_patch(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json=asset_payload(
            asset_id=1,
            custom_fields={
                "CPU": {"field": "_snipeit_cpu_1", "value": "i5-10400"},
                "CPU PassMark Score": {"field": "_snipeit_cpu_passmark_score_2", "value": "8000"},
                "RAM (GB)": {"field": "_snipeit_ram_gb_3", "value": "16"},
                "Storage (GB)": {"field": "_snipeit_storage_gb_4", "value": "512"},
                "Sale Price": {"field": "_snipeit_sale_price_5", "value": ""},
                "Touchscreen": {"field": "_snipeit_touchscreen_6", "value": "0"},
            },
        ),
    )
    result = runner.invoke(
        app,
        ["--config", str(config_file), "assets", "price", "--id", "1", "--dry-run"],
    )
    assert result.exit_code == 0, result.stderr
    assert "Dry run" in result.stderr
    # No PATCH should have been attempted.
    assert not [r for r in httpx_mock.get_requests() if r.method == "PATCH"]


def test_price_passmark_override_takes_priority(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    """Source 1 of the PassMark resolution chain: --passmark CLI override."""
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json=asset_payload(
            asset_id=1,
            custom_fields={
                "CPU": {"field": "_snipeit_cpu_1", "value": "i5-10400"},
                # Existing value should be ignored when --passmark is supplied.
                "CPU PassMark Score": {"field": "_snipeit_cpu_passmark_score_2", "value": "1000"},
                "RAM (GB)": {"field": "_snipeit_ram_gb_3", "value": "16"},
                "Storage (GB)": {"field": "_snipeit_storage_gb_4", "value": "512"},
                "Sale Price": {"field": "_snipeit_sale_price_5", "value": ""},
                "Touchscreen": {"field": "_snipeit_touchscreen_6", "value": "0"},
            },
        ),
    )
    httpx_mock.add_response(
        method="PATCH",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json={"status": "success", "payload": asset_payload(asset_id=1)},
    )
    result = runner.invoke(
        app,
        [
            "--config", str(config_file),
            "assets", "price",
            "--id", "1",
            "--passmark", "20000",
        ],
    )
    assert result.exit_code == 0, result.stderr
    # 20 + 3 + 4 = 27 → tier $250.
    assert "$250" in result.stdout

    patches = [r for r in httpx_mock.get_requests() if r.method == "PATCH"]
    body = json.loads(patches[0].content)
    assert body["_snipeit_cpu_passmark_score_2"] == "20000"


def test_price_missing_ram_in_asset_errors_out(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/1",
        json=asset_payload(
            asset_id=1,
            custom_fields={
                "CPU": {"field": "_snipeit_cpu_1", "value": "i5-10400"},
                "CPU PassMark Score": {"field": "_snipeit_cpu_passmark_score_2", "value": "8000"},
                "RAM (GB)": {"field": "_snipeit_ram_gb_3", "value": ""},
                "Storage (GB)": {"field": "_snipeit_storage_gb_4", "value": "512"},
                "Sale Price": {"field": "_snipeit_sale_price_5", "value": ""},
                "Touchscreen": {"field": "_snipeit_touchscreen_6", "value": "0"},
            },
        ),
    )
    result = runner.invoke(
        app,
        ["--config", str(config_file), "assets", "price", "--id", "1"],
    )
    assert result.exit_code == 1
    assert "no RAM value" in result.stderr.lower() or "ram" in result.stderr.lower()


# ── version subcommand ───────────────────────────────────────────────────────


def test_version_command(runner: CliRunner) -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.startswith("inventory ")


def test_version_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.startswith("inventory ")
