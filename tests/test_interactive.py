"""CLI entry-point tests for guided interactive mode."""

from __future__ import annotations

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
