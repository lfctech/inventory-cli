"""CLI entry-point tests for guided interactive mode."""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from inventory.main import app

from .conftest import TEST_URL, asset_payload

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("inventory.interactive._is_tty", lambda: True)


def test_interactive_command_can_exit(
    runner: CliRunner, fake_env, reset_state, config_file
) -> None:
    result = runner.invoke(
        app,
        ["--config", str(config_file), "interactive"],
        input="5\n",
    )

    assert result.exit_code == 0, result.output
    assert "Find an asset" in result.output
    assert "Save an asset label" in result.output


def test_interactive_short_flag_can_exit(
    runner: CliRunner, fake_env, reset_state, config_file
) -> None:
    result = runner.invoke(
        app,
        ["--config", str(config_file), "-i"],
        input="5\n",
    )

    assert result.exit_code == 0, result.output
    assert "Inventory" in result.output


def test_interactive_rejects_json(
    runner: CliRunner, fake_env, reset_state, config_file
) -> None:
    result = runner.invoke(
        app,
        ["--config", str(config_file), "--json", "interactive"],
    )

    assert result.exit_code == 2
    assert "--json cannot be used with interactive mode" in result.output


def test_bare_inventory_still_shows_help(runner: CliRunner, fake_env, reset_state) -> None:
    result = runner.invoke(app, [])

    assert result.exit_code == 2
    assert "Usage:" in result.output
    assert "interactive" in result.output


def test_global_option_without_command_shows_help(runner: CliRunner, fake_env, reset_state) -> None:
    result = runner.invoke(app, ["--url", TEST_URL])

    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_interactive_flag_rejects_subcommand(runner: CliRunner, fake_env, reset_state) -> None:
    result = runner.invoke(app, ["-i", "version"])

    assert result.exit_code == 2
    assert "--interactive cannot be combined with a subcommand" in result.output


def test_find_uses_one_prompt_for_tag_and_serial(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/hardware/bytag/SCAN-1",
        status_code=404,
        json={"status": "error", "messages": "not found"},
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(rf"^{re.escape(TEST_URL)}/api/v1/hardware/byserial/SCAN-1"),
        json={"total": 1, "rows": [asset_payload(asset_id=9, serial="SCAN-1")]},
    )

    result = runner.invoke(
        app,
        ["--config", str(config_file), "interactive"],
        input="1\nSCAN-1\n0\n5\n",
    )

    assert result.exit_code == 0, result.output
    assert "Scan or enter asset tag/serial" in result.output
    assert "LFC-1" in result.output


def test_add_asset_uses_auto_assigned_tag(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=re.compile(rf"^{re.escape(TEST_URL)}/api/v1/models\b"),
        json={"total": 1, "rows": [{"id": 5, "name": "Latitude 5440"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(rf"^{re.escape(TEST_URL)}/api/v1/statuslabels\b"),
        json={"total": 1, "rows": [{"id": 9, "name": "Ready"}]},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_URL}/api/v1/hardware",
        json={"status": "success", "payload": asset_payload(asset_id=42, asset_tag="LFC-42")},
    )

    result = runner.invoke(
        app,
        ["--config", str(config_file), "interactive"],
        input="2\nLatitude\n1\nReady\n1\nSERIAL-42\ny\n0\n5\n",
    )

    assert result.exit_code == 0, result.output
    assert "Asset created: LFC-42" in result.output
    post = next(request for request in httpx_mock.get_requests() if request.method == "POST")
    assert json.loads(post.content) == {"model_id": 5, "status_id": 9, "serial": "SERIAL-42"}


def test_update_shows_before_after_review_and_saves_name(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    original = asset_payload(asset_id=9, asset_tag="LFC-9", name="Old Name")
    updated = asset_payload(asset_id=9, asset_tag="LFC-9", name="New Name")
    httpx_mock.add_response(
        method="GET", url=f"{TEST_URL}/api/v1/hardware/bytag/LFC-9", json=original
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(rf"^{re.escape(TEST_URL)}/api/v1/hardware/byserial/LFC-9"),
        status_code=404,
        json={"status": "error", "messages": "not found"},
    )
    httpx_mock.add_response(
        method="PATCH",
        url=f"{TEST_URL}/api/v1/hardware/9",
        json={"status": "success", "payload": updated},
    )
    httpx_mock.add_response(method="GET", url=f"{TEST_URL}/api/v1/hardware/9", json=updated)

    result = runner.invoke(
        app,
        ["--config", str(config_file), "interactive"],
        input="3\nLFC-9\n3\n1\nNew Name\n10\ny\n0\n5\n",
    )

    assert result.exit_code == 0, result.output
    assert "Current" in result.output
    assert "Old Name" in result.output
    assert "New Name" in result.output
    assert "Asset LFC-9 updated" in result.output


def test_invalid_number_reprompts_without_losing_staged_changes(
    runner: CliRunner, fake_env, reset_state, config_file, httpx_mock
) -> None:
    original = asset_payload(asset_id=9, asset_tag="LFC-9", name="Old Name")
    updated = asset_payload(asset_id=9, asset_tag="LFC-9", name="New Name")
    httpx_mock.add_response(
        method="GET", url=f"{TEST_URL}/api/v1/hardware/bytag/LFC-9", json=original
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(rf"^{re.escape(TEST_URL)}/api/v1/hardware/byserial/LFC-9"),
        status_code=404,
        json={"status": "error", "messages": "not found"},
    )
    httpx_mock.add_response(
        method="PATCH",
        url=f"{TEST_URL}/api/v1/hardware/9",
        json={"status": "success", "payload": updated},
    )
    httpx_mock.add_response(method="GET", url=f"{TEST_URL}/api/v1/hardware/9", json=updated)

    result = runner.invoke(
        app,
        ["--config", str(config_file), "interactive"],
        input="3\nLFC-9\n3\n1\nNew Name\n5\n1\n16gb\n16\n10\ny\n0\n5\n",
    )

    assert result.exit_code == 0, result.output
    assert "Enter a numeric value" in result.output
    patch = next(request for request in httpx_mock.get_requests() if request.method == "PATCH")
    body = json.loads(patch.content)
    assert body["name"] == "New Name"
    assert body["_snipeit_ram_gb_3"] == "16"
