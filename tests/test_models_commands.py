"""Tier 2: CLI command tests for ``inventory models``."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from inventory.main import app

from .conftest import TEST_URL, strip_ansi

pytestmark = pytest.mark.unit


def test_get_json_validation_error_is_machine_readable(
    runner: CliRunner, fake_env, reset_state
) -> None:
    result = runner.invoke(app, ["--json", "models", "get"])

    assert result.exit_code == 1
    assert json.loads(strip_ansi(result.stdout)) == {
        "error": "Provide either --id or --name."
    }
    assert result.stderr == ""


def test_delete_json_prompt_does_not_corrupt_stdout(
    runner: CliRunner, fake_env, reset_state, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/models/7",
        json={"id": 7, "name": "Latitude 5440"},
    )
    httpx_mock.add_response(
        method="DELETE",
        url=f"{TEST_URL}/api/v1/models/7",
        json={"status": "success"},
    )

    result = runner.invoke(
        app,
        ["--json", "models", "delete", "--id", "7"],
        input="y\n",
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(strip_ansi(result.stdout)) == {"status": "success"}
    assert "delete model 7" in strip_ansi(result.stderr)


def test_update_no_changes_json_is_machine_readable(
    runner: CliRunner, fake_env, reset_state, httpx_mock
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{TEST_URL}/api/v1/models/7",
        json={"id": 7, "name": "Latitude 5440"},
    )

    result = runner.invoke(
        app,
        ["--json", "models", "update", "--id", "7"],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(strip_ansi(result.stdout)) == {
        "status": "warning",
        "message": "No fields to update.",
    }
    assert result.stderr == ""
