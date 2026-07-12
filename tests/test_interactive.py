"""CLI entry-point tests for guided interactive mode."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

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


def test_enhanced_menu_uses_keyboard_select_and_back_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.return_value = 1
    select = Mock(return_value=prompt)
    api = SimpleNamespace(select=select, fuzzy=Mock())
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    assert interactive._choose("Menu", ["First", "Second"]) == 1

    choices = select.call_args.kwargs["choices"]
    assert [choice.name for choice in choices] == ["First", "Second", "← Back"]
    assert "↑/↓" in select.call_args.kwargs["instruction"]


def test_enhanced_menu_uses_fuzzy_filter_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.return_value = 0
    fuzzy = Mock(return_value=prompt)
    api = SimpleNamespace(select=Mock(), fuzzy=fuzzy)
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    assert interactive._choose("Fields", ["One", "Two"], fuzzy=True) == 0
    fuzzy.assert_called_once()


def test_page_clears_and_redraws_in_enhanced_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    clear = Mock()
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive.console, "clear", clear)

    interactive._page("Asset details", "LFC-1")

    clear.assert_called_once_with()


def test_enhanced_menu_back_choice_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.return_value = None
    api = SimpleNamespace(select=Mock(return_value=prompt), fuzzy=Mock())
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    assert interactive._choose("Menu", ["Continue"]) is None


def test_enhanced_text_back_redraws_current_page(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.return_value = ":back"
    api = SimpleNamespace(text=Mock(return_value=prompt))
    redraw = Mock()
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)
    monkeypatch.setattr(interactive, "_redraw_page", redraw)

    assert interactive._prompt("Serial") is interactive.BACK
    redraw.assert_called_once_with()


def test_enhanced_confirmation_delegates_to_inquirer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.return_value = True
    confirm = Mock(return_value=prompt)
    api = SimpleNamespace(confirm=confirm)
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    assert interactive._confirm("Continue?", default=True) is True
    assert confirm.call_args.kwargs["default"] is True


def test_ctrl_c_goes_back_from_text_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.side_effect = KeyboardInterrupt
    api = SimpleNamespace(text=Mock(return_value=prompt))
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)
    monkeypatch.setattr(interactive, "_redraw_page", Mock())

    assert interactive._prompt("Serial") is interactive.BACK


def test_ctrl_c_goes_back_from_submenu_but_exits_main_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.side_effect = KeyboardInterrupt
    api = SimpleNamespace(select=Mock(return_value=prompt), fuzzy=Mock())
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    assert interactive._choose("Submenu", ["Continue"], back=True) is None
    with pytest.raises(KeyboardInterrupt):
        interactive._choose("Main", ["Exit"], back=False)


def test_ctrl_c_goes_back_from_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.side_effect = KeyboardInterrupt
    api = SimpleNamespace(confirm=Mock(return_value=prompt))
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    assert interactive._confirm("Save?") is interactive.BACK


def test_add_back_from_replacement_model_preserves_draft(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    service = Mock()
    session = interactive.InteractiveSession(service)
    model = SimpleNamespace(id=5, name="Latitude")
    status = SimpleNamespace(id=9, name="Ready")
    session.pick_model = Mock(side_effect=[model, interactive.BACK])
    session.pick_resource = Mock(side_effect=[interactive.BACK, status])
    monkeypatch.setattr(interactive, "_choose", Mock(side_effect=[1, 0]))
    monkeypatch.setattr(interactive, "_prompt", Mock(return_value="SN-1"))
    monkeypatch.setattr(interactive, "_confirm", Mock(return_value=False))
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_print_review", Mock())

    session.add_asset()

    assert session.pick_model.call_count == 2
    service.create_asset.assert_not_called()


def test_add_back_from_final_confirmation_revisits_serial_without_write(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    service = Mock()
    session = interactive.InteractiveSession(service)
    session.pick_model = Mock(return_value=SimpleNamespace(id=5, name="Latitude"))
    session.pick_resource = Mock(return_value=SimpleNamespace(id=9, name="Ready"))
    prompt = Mock(side_effect=["SN-1", "SN-1"])
    monkeypatch.setattr(interactive, "_prompt", prompt)
    monkeypatch.setattr(interactive, "_confirm", Mock(side_effect=[interactive.BACK, False]))
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_print_review", Mock())

    session.add_asset()

    assert prompt.call_count == 2
    assert prompt.call_args_list[1].kwargs["default"] == "SN-1"
    service.create_asset.assert_not_called()


def test_ambiguous_lookup_back_returns_to_identifier_prompt(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from snipeit.resources.assets import Asset

    from inventory import interactive
    from inventory.application import AssetMatches
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    first = cast(Asset, SimpleNamespace(id=1, asset_tag="TAG-1", serial="S1", model={}))
    second = cast(Asset, SimpleNamespace(id=2, asset_tag="TAG-2", serial="S2", model={}))
    service = Mock()
    service.find_asset.return_value = AssetMatches(tag=first, serial=second)
    session = interactive.InteractiveSession(service)
    prompt = Mock(side_effect=["MATCH", interactive.BACK])
    monkeypatch.setattr(interactive, "_prompt", prompt)
    monkeypatch.setattr(interactive, "_choose", Mock(return_value=None))
    monkeypatch.setattr(interactive, "_page", Mock())

    assert session.lookup() is None
    assert prompt.call_count == 2


def test_label_output_back_returns_to_selected_asset_confirmation(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    session = interactive.InteractiveSession(Mock())
    asset = SimpleNamespace(id=1, asset_tag="LFC-1", serial="SN", model={"name": "Model"})
    session.lookup = Mock(return_value=asset)
    session.save_label = Mock(return_value=False)
    monkeypatch.setattr(interactive, "_confirm", Mock(side_effect=[True, False]))
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_print_asset", Mock())

    session.label_asset()

    session.lookup.assert_called_once_with()
    session.save_label.assert_called_once_with(asset)


def test_clear_edit_draft_mutates_caller_owned_state() -> None:
    from inventory import interactive

    draft = interactive.AssetEditDraft(
        changes={"name": "New"},
        review_values={"name": "New"},
        model=SimpleNamespace(id=5),
    )

    interactive._clear_edit_draft(draft)

    assert draft.changes == {}
    assert draft.review_values == {}
    assert draft.model is None


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
        input="1\nSCAN-1\n0\n:back\n5\n",
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
        input="3\nLFC-9\n1\n3\n1\nNew Name\n10\ny\n0\n5\n",
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
        input="3\nLFC-9\n1\n3\n1\nNew Name\n5\n1\n16gb\n16\n10\ny\n0\n5\n",
    )

    assert result.exit_code == 0, result.output
    assert "Enter a numeric value" in result.output
    patch = next(request for request in httpx_mock.get_requests() if request.method == "PATCH")
    body = json.loads(patch.content)
    assert body["name"] == "New Name"
    assert body["_snipeit_ram_gb_3"] == "16"
