"""CLI entry-point tests for guided interactive mode."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call

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


def test_interactive_rejects_json(runner: CliRunner, fake_env, reset_state, config_file) -> None:
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


def test_enhanced_menu_uses_keyboard_select_without_back_choice(
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
    assert [choice.name for choice in choices] == ["First", "Second"]
    assert select.call_args.kwargs["instruction"] == ""
    assert interactive._current_page is not None
    controls = interactive._current_page[2]
    assert controls is not None
    assert "Esc back" in controls
    prompt.register_kb.assert_called_once()


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
    prompt.register_kb.assert_called_once()
    assert interactive._current_page is not None
    assert interactive._current_page[1] is None
    assert interactive._current_page[2] == interactive._FUZZY_HELP


def test_page_clears_and_redraws_in_enhanced_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    clear = Mock()
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive.console, "clear", clear)

    interactive._page("Asset details", "LFC-1")

    clear.assert_called_once_with()


def test_page_renders_title_then_one_contextual_help_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inventory import interactive

    printed = Mock()
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: False)
    monkeypatch.setattr(interactive.console, "print", printed)

    interactive._page("Search fieldsets", "Type a search term", controls=interactive._TEXT_HELP)

    lines = [call.args[0] for call in printed.call_args_list if call.args]
    assert lines[:3] == [
        "[bold]Search fieldsets[/bold]",
        "[dim]Enter submit • Esc back[/dim]",
        "[dim]Type a search term[/dim]",
    ]
    assert len([line for line in lines if "Esc back" in line]) == 1


def test_escape_timeout_is_tuned_for_each_prompt() -> None:
    from inventory import interactive

    application = SimpleNamespace(ttimeoutlen=0.5, timeoutlen=1.0)
    interactive._tune_escape_timeout(SimpleNamespace(application=application))

    assert application.ttimeoutlen == interactive._ESCAPE_TIMEOUT_SECONDS
    assert application.timeoutlen == interactive._ESCAPE_TIMEOUT_SECONDS


def test_resource_search_back_cancels_without_searching(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    service = Mock()
    session = interactive.InteractiveSession(service)
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_prompt", Mock(return_value=interactive.BACK))

    assert session.pick_resource("models", "Search models", allow_create=True) is interactive.BACK
    service.search.assert_not_called()


def test_escape_binding_returns_back_without_changing_ctrl_c_behavior() -> None:
    from prompt_toolkit.keys import Keys

    from inventory import interactive

    class Prompt:
        handler: Any

        def __init__(self):
            self.status = {"answered": False, "result": None}

        def register_kb(self, key):
            assert key == Keys.Escape

            def decorator(handler):
                self.handler = handler
                return handler

            return decorator

    class App:
        result: Any

        def exit(self, *, result):
            self.result = result

    prompt = Prompt()
    interactive._install_back_binding(prompt)
    app = App()
    prompt.handler(SimpleNamespace(app=app))

    assert prompt.status["result"] is interactive.BACK
    assert app.result is interactive.BACK


def test_enhanced_menu_escape_returns_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.return_value = interactive.BACK
    api = SimpleNamespace(select=Mock(return_value=prompt), fuzzy=Mock())
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    assert interactive._choose("Menu", ["Continue"]) is interactive.BACK
    prompt.register_kb.assert_called_once()


def test_enhanced_text_colon_back_remains_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.return_value = ":back"
    api = SimpleNamespace(text=Mock(return_value=prompt))
    redraw = Mock()
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)
    monkeypatch.setattr(interactive, "_redraw_page", redraw)

    assert interactive._prompt("Serial") is interactive.BACK
    redraw.assert_not_called()


def test_enhanced_text_escape_result_is_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.return_value = interactive.BACK
    api = SimpleNamespace(text=Mock(return_value=prompt))
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    assert interactive._prompt("Serial") is interactive.BACK
    prompt.register_kb.assert_called_once()


def test_enhanced_text_prompt_has_no_embedded_control_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.return_value = "serial"
    text = Mock(return_value=prompt)
    api = SimpleNamespace(text=text)
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    assert interactive._prompt("Serial") == "serial"
    assert text.call_args.kwargs["instruction"] == ""
    assert "Ctrl+C" not in text.call_args.kwargs["mandatory_message"]


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
    assert "Ctrl+C" not in confirm.call_args.kwargs["instruction"]
    prompt.register_kb.assert_called_once()


def test_fallback_menu_hides_selectable_back_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    output = Mock()
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: False)
    monkeypatch.setattr(interactive.console, "print", output)
    monkeypatch.setattr(interactive.typer, "prompt", Mock(return_value=":back"))

    assert interactive._choose("Menu", ["Continue"]) is interactive.BACK
    rendered = " ".join(str(call.args[0]) for call in output.call_args_list if call.args)
    assert "Back" not in rendered


def test_ctrl_c_propagates_from_text_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.side_effect = KeyboardInterrupt
    api = SimpleNamespace(text=Mock(return_value=prompt))
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)
    with pytest.raises(KeyboardInterrupt):
        interactive._prompt("Serial")


def test_ctrl_c_propagates_from_submenu_and_main_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.side_effect = KeyboardInterrupt
    api = SimpleNamespace(select=Mock(return_value=prompt), fuzzy=Mock())
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    with pytest.raises(KeyboardInterrupt):
        interactive._choose("Submenu", ["Continue"], back=True)
    with pytest.raises(KeyboardInterrupt):
        interactive._choose("Main", ["Exit"], back=False)


def test_ctrl_c_propagates_from_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    prompt = Mock()
    prompt.execute.side_effect = KeyboardInterrupt
    api = SimpleNamespace(confirm=Mock(return_value=prompt))
    monkeypatch.setattr(interactive, "_enhanced_prompts", lambda: True)
    monkeypatch.setattr(interactive, "_inquirer", api)

    with pytest.raises(KeyboardInterrupt):
        interactive._confirm("Save?")


def test_update_asset_uses_yes_no_confirmation_before_editing(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    session = interactive.InteractiveSession(Mock())
    asset = cast(Any, SimpleNamespace(asset_tag="LFC-1", serial="SN", model={}))
    session.lookup = Mock(side_effect=[asset, None])
    session.edit_asset = Mock()
    confirm = Mock(return_value=False)
    choose = Mock()
    monkeypatch.setattr(interactive, "_confirm", confirm)
    monkeypatch.setattr(interactive, "_choose", choose)
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_print_asset", Mock())

    session.update_asset()

    confirm.assert_called_once_with("Edit this asset?", default=True)
    choose.assert_not_called()
    session.edit_asset.assert_not_called()


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
    session.lookup = Mock(side_effect=[asset, None])
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


def test_add_label_failure_retries_label_without_recreating_asset(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    asset = SimpleNamespace(id=42, asset_tag="LFC-42", serial="SN-1", model={})
    output = config_file.parent / "label-LFC-42.pdf"
    service = Mock()
    service.create_asset.return_value = (asset, SimpleNamespace(rollback_errors=[]))
    service.save_label.side_effect = [OSError("disk full"), str(output)]
    session = interactive.InteractiveSession(service)
    session.pick_model = Mock(return_value=SimpleNamespace(id=5, name="Latitude"))
    session.pick_resource = Mock(return_value=SimpleNamespace(id=9, name="Ready"))
    choices = Mock(side_effect=[0, 0, interactive.BACK])
    printed = Mock()
    monkeypatch.setattr(interactive, "_choose", choices)
    monkeypatch.setattr(
        interactive, "_prompt", Mock(side_effect=["SN-1", str(output), str(output)])
    )
    monkeypatch.setattr(interactive, "_confirm", Mock(return_value=True))
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_print_review", Mock())
    monkeypatch.setattr(interactive.console, "print", printed)

    session.add_asset()

    service.create_asset.assert_called_once_with(status_id=9, serial="SN-1", model_id=5)
    assert service.save_label.call_count == 2
    assert [attempt.args for attempt in service.save_label.call_args_list] == [
        (asset, output),
        (asset, output),
    ]
    choices.assert_any_call("Label not saved", ["Retry saving this label"], clear=False)
    messages = [str(attempt.args[0]) for attempt in printed.call_args_list]
    assert any("disk full" in message for message in messages)
    assert any("Asset LFC-42 is saved" in message for message in messages)


def test_add_label_failure_escape_keeps_saved_asset_without_retry(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    asset = SimpleNamespace(id=42, asset_tag="LFC-42", serial="SN-1", model={})
    output = config_file.parent / "label-LFC-42.pdf"
    service = Mock()
    service.create_asset.return_value = (asset, SimpleNamespace(rollback_errors=[]))
    service.save_label.side_effect = OSError("disk full")
    session = interactive.InteractiveSession(service)
    session.pick_model = Mock(return_value=SimpleNamespace(id=5, name="Latitude"))
    session.pick_resource = Mock(return_value=SimpleNamespace(id=9, name="Ready"))
    choices = Mock(side_effect=[0, interactive.BACK, interactive.BACK])
    monkeypatch.setattr(interactive, "_choose", choices)
    monkeypatch.setattr(
        interactive, "_prompt", Mock(side_effect=["SN-1", str(output)])
    )
    monkeypatch.setattr(interactive, "_confirm", Mock(return_value=True))
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_print_review", Mock())

    session.add_asset()

    service.create_asset.assert_called_once_with(status_id=9, serial="SN-1", model_id=5)
    service.save_label.assert_called_once_with(asset, output)
    assert choices.call_args_list[-2].args == (
        "Label not saved",
        ["Retry saving this label"],
    )


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
        input="3\nLFC-9\ny\n3\n1\nNew Name\n10\ny\n0\n5\n",
    )

    assert result.exit_code == 0, result.output
    assert "Current" in result.output
    assert "Old Name" in result.output
    assert "New Name" in result.output
    assert "Edit this asset?" in result.output
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
        input="3\nLFC-9\ny\n3\n1\nNew Name\n5\n1\n16gb\n16\n10\ny\n0\n5\n",
    )

    assert result.exit_code == 0, result.output
    assert "Enter a numeric value" in result.output
    patch = next(request for request in httpx_mock.get_requests() if request.method == "PATCH")
    body = json.loads(patch.content)
    assert body["name"] == "New Name"
    assert body["_snipeit_ram_gb_3"] == "16"


def test_find_label_failure_stays_on_asset_without_parent_retry(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    asset = SimpleNamespace(id=42, asset_tag="LFC-42", serial="SN-1", model={})
    service = Mock()
    service.save_label.side_effect = OSError("disk full")
    session = interactive.InteractiveSession(service)
    session.lookup = Mock(side_effect=[asset, None])
    monkeypatch.setattr(
        interactive,
        "_choose",
        Mock(side_effect=[1, interactive.BACK, interactive.BACK]),
    )
    monkeypatch.setattr(interactive, "_prompt", Mock(return_value="label.pdf"))
    monkeypatch.setattr(interactive, "_confirm", Mock(return_value=True))
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_print_asset", Mock())

    session.find_asset()

    assert session.lookup.call_count == 2
    service.save_label.assert_called_once_with(asset, Path("label.pdf"))


def test_direct_label_failure_can_cancel_without_repeating_lookup(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    asset = SimpleNamespace(id=42, asset_tag="LFC-42", serial="SN-1", model={})
    service = Mock()
    service.save_label.side_effect = OSError("disk full")
    session = interactive.InteractiveSession(service)
    session.lookup = Mock(side_effect=[asset, None])
    monkeypatch.setattr(interactive, "_choose", Mock(return_value=interactive.BACK))
    monkeypatch.setattr(interactive, "_prompt", Mock(return_value="label.pdf"))
    monkeypatch.setattr(interactive, "_confirm", Mock(side_effect=[True, False]))
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_print_asset", Mock())

    session.label_asset()

    session.lookup.assert_called_once_with()
    service.save_label.assert_called_once_with(asset, Path("label.pdf"))


def test_numeric_editor_rejects_negative_and_non_finite_values(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    session = interactive.InteractiveSession(Mock())
    monkeypatch.setattr(interactive, "_choose", Mock(return_value=0))
    monkeypatch.setattr(interactive, "_page", Mock())

    monkeypatch.setattr(interactive, "_prompt", Mock(side_effect=["-8", "16"]))
    assert session._edit_value("ram") == 16
    monkeypatch.setattr(
        interactive,
        "_prompt",
        Mock(side_effect=["nan", "inf", "-1", "125"]),
    )
    assert session._edit_value("sale_price") == 125


def test_lookup_retries_same_identifier_after_api_error(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from snipeit.exceptions import SnipeITTimeoutError
    from snipeit.resources.assets import Asset

    from inventory import interactive
    from inventory.application import AssetMatches
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    asset = SimpleNamespace(id=42, asset_tag="LFC-42", serial="SN-1", model={})
    service = Mock()
    service.find_asset.side_effect = [
        SnipeITTimeoutError("timed out"),
        AssetMatches(tag=cast(Asset, asset)),
    ]
    session = interactive.InteractiveSession(service)
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_prompt", Mock(side_effect=["SCAN-1", "SCAN-1"]))

    assert session.lookup() is asset
    assert service.find_asset.call_args_list == [call("SCAN-1"), call("SCAN-1")]


def test_resource_search_retries_same_query_after_api_error(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from snipeit.exceptions import SnipeITTimeoutError

    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    result = SimpleNamespace(id=5, name="Latitude")
    service = Mock()
    service.search.side_effect = [SnipeITTimeoutError("timed out"), [result]]
    session = interactive.InteractiveSession(service)
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_prompt", Mock(side_effect=["Latitude", "Latitude"]))
    monkeypatch.setattr(interactive, "_choose", Mock(return_value=0))

    assert session.pick_resource("models", "Search models", allow_create=False) is result
    assert service.search.call_args_list == [call("models", "Latitude"), call("models", "Latitude")]


def test_update_review_includes_all_new_model_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from inventory import interactive

    asset = Mock(model={"name": "Old"}, status_label={"name": "Ready"}, name="")
    asset.get_custom_field.return_value = ""
    config = SimpleNamespace(
        custom_fields=SimpleNamespace(
            cpu_model="CPU",
            ram="RAM",
            storage="Storage",
            touch_screen="Touchscreen",
            cpu_passmark="PassMark",
            sale_price="Sale Price",
        )
    )
    model = interactive.NewModel(
        name="New Model",
        category_id=2,
        category_name="Laptops",
        manufacturer_name="Acme",
        manufacturer_display="Acme",
        fieldset_name="Refurbishing",
        fieldset_id=3,
        model_number="NM-1",
        notes="New notes",
    )
    table = Mock()
    monkeypatch.setattr(interactive, "Table", Mock(return_value=table))

    interactive._print_update_review(
        asset,
        config,
        {"model": "New Model", **interactive._new_model_review_values(model)},
    )

    rendered = " ".join(str(call.args) for call in table.add_row.call_args_list)
    for value in ("Acme", "Laptops", "Refurbishing", "NM-1", "New notes"):
        assert value in rendered


def test_update_menu_only_offers_fields_in_asset_fieldset(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file
) -> None:
    from inventory import interactive
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    session = interactive.InteractiveSession(Mock())
    asset = cast(
        Any,
        SimpleNamespace(
            asset_tag="LFC-1",
            serial="SN-1",
            model={"name": "Model"},
            custom_fields={"CPU": {"field": "_snipeit_cpu_1", "value": ""}},
        ),
    )
    choose = Mock(return_value=interactive.BACK)
    monkeypatch.setattr(interactive, "_choose", choose)

    assert session.edit_asset(asset) is interactive.BACK

    offered = choose.call_args.args[1]
    assert offered == ["Model", "Status", "Name", "CPU", "Review changes"]


@pytest.mark.parametrize("cleanup_interrupted", [False, True])
def test_transaction_interrupt_reports_outcome_then_exits_instead_of_reopening_menu(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file, cleanup_interrupted
) -> None:
    from inventory import interactive
    from inventory.application import CreatedResources, MutationOutcome, TransactionError
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    session = interactive.InteractiveSession(Mock())
    error = TransactionError(
        ValueError("rejected") if cleanup_interrupted else KeyboardInterrupt(),
        [],
        outcome=MutationOutcome.AMBIGUOUS,
        created=CreatedResources(cleanup_interrupted=cleanup_interrupted),
    )
    session.add_asset = Mock(side_effect=error)
    session._print_transaction_error = Mock()
    choose = Mock(return_value=1)
    monkeypatch.setattr(interactive, "_choose", choose)

    with pytest.raises(KeyboardInterrupt):
        session.run()

    session._print_transaction_error.assert_called_once_with(error)
    assert choose.call_count == 1


@pytest.mark.parametrize("refresh_verified", [True, False])
def test_post_update_recovery_never_repeats_the_saved_update(
    monkeypatch: pytest.MonkeyPatch, reset_state, config_file, refresh_verified
) -> None:
    from snipeit.exceptions import SnipeITTimeoutError

    from inventory import interactive
    from inventory.application import CreatedResources
    from inventory.config import load_config
    from inventory.main import state

    state.config = load_config(config_file)
    asset = cast(
        Any,
        SimpleNamespace(
            id=42, asset_tag="LFC-42", serial="SN", model={}, custom_fields={}, refresh=Mock()
        ),
    )
    created = CreatedResources(refresh_verified=refresh_verified)
    service = Mock()
    service.update_asset.return_value = (asset, created)
    session = interactive.InteractiveSession(service)
    session.save_label = Mock(side_effect=[SnipeITTimeoutError("label timeout"), True])
    choices = [3] + ([] if refresh_verified else [0]) + [0, 0]
    monkeypatch.setattr(interactive, "_choose", Mock(side_effect=choices))
    monkeypatch.setattr(interactive, "_confirm", Mock(return_value=True))
    monkeypatch.setattr(interactive, "_page", Mock())
    monkeypatch.setattr(interactive, "_print_asset", Mock())
    monkeypatch.setattr(interactive, "_print_update_review", Mock())
    draft = interactive.AssetEditDraft(
        changes={"name": "Changed"}, review_values={"name": "Changed"}
    )

    assert session.edit_asset(asset, draft) is asset

    service.update_asset.assert_called_once_with(asset, state.config, changes={"name": "Changed"})
    assert session.save_label.call_args_list == [call(asset), call(asset)]
    assert asset.refresh.call_count == (0 if refresh_verified else 1)
