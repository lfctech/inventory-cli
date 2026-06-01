"""Tier 1: tests for ``inventory.config``."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from inventory.config import (
    PricingTier,
    _parse_tiers,
    _xdg_config_path,
    load_config,
    resolve_config_path,
)
from inventory.main import _configure_logging, app

pytestmark = pytest.mark.unit


# ── _parse_tiers ─────────────────────────────────────────────────────────────


def test_parse_tiers_basic() -> None:
    raw = [[6, 100], [10, 125], [14, 150]]
    tiers = _parse_tiers(raw)
    assert tiers == [
        PricingTier(6, 100),
        PricingTier(10, 125),
        PricingTier(14, 150),
    ]


def test_parse_tiers_sorts_ascending() -> None:
    """Out-of-order tiers must be sorted so price_from_points works."""
    raw = [[14, 150], [6, 100], [10, 125]]
    tiers = _parse_tiers(raw)
    assert [t.max_points for t in tiers] == [6, 10, 14]


def test_parse_tiers_rejects_malformed_entry() -> None:
    with pytest.raises(ValueError, match=r"\[max_points, price\]"):
        _parse_tiers([[6, 100], [10]])  # second entry is one element short


def test_parse_tiers_rejects_three_element_entry() -> None:
    with pytest.raises(ValueError, match=r"\[max_points, price\]"):
        _parse_tiers([[6, 100, 999]])


def test_parse_tiers_coerces_strings_to_ints() -> None:
    """TOML allows numeric-strings; ``int(...)`` should accept them so we
    don't crash on user-typed configs that quoted the values by accident."""
    # The function annotates ``list[list[int]]`` but defensively calls
    # ``int(...)`` on each element. Cast suppresses the deliberate type
    # widening for this defence-in-depth check.
    raw = cast("list[list[int]]", [["6", "100"]])
    tiers = _parse_tiers(raw)
    assert tiers == [PricingTier(6, 100)]


# ── _xdg_config_path ─────────────────────────────────────────────────────────


def test_xdg_config_path_uses_xdg_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert _xdg_config_path() == tmp_path / "inventory" / "config.toml"


def test_xdg_config_path_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    p = _xdg_config_path()
    # Don't pin an absolute path (CI vs local home differ); just check
    # the suffix.
    assert p.parts[-3:] == (".config", "inventory", "config.toml")


# ── resolve_config_path (three-tier fallback) ────────────────────────────────


def test_resolve_explicit_flag_found(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("")
    assert resolve_config_path(str(p)) == p.resolve()


def test_resolve_explicit_flag_missing_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.toml"
    with pytest.raises(ValueError, match="not found"):
        resolve_config_path(str(missing))


def test_resolve_env_var_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("INVENTORY_CONFIG", str(p))
    assert resolve_config_path(None) == p.resolve()


def test_resolve_env_var_missing_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INVENTORY_CONFIG", str(tmp_path / "nope.toml"))
    with pytest.raises(ValueError, match="not found"):
        resolve_config_path(None)


def test_resolve_xdg_fallback_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("INVENTORY_CONFIG", raising=False)
    cfg = tmp_path / "inventory" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("")
    assert resolve_config_path(None) == cfg


def test_resolve_xdg_fallback_absent_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("INVENTORY_CONFIG", raising=False)
    # No file written.
    assert resolve_config_path(None) is None


def test_resolve_priority_flag_beats_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If both --config and INVENTORY_CONFIG are set, --config wins."""
    flag_cfg = tmp_path / "flag.toml"
    flag_cfg.write_text("")
    env_cfg = tmp_path / "env.toml"
    env_cfg.write_text("")
    monkeypatch.setenv("INVENTORY_CONFIG", str(env_cfg))
    assert resolve_config_path(str(flag_cfg)) == flag_cfg.resolve()


# ── load_config ──────────────────────────────────────────────────────────────


def test_load_config_full(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        """
[snipeit]
url = "https://example.invalid"
timeout = 30
max_retries = 5

[custom_fields]
cpu_model     = "Processor"
cpu_passmark  = "Score"
ram           = "Memory"
storage       = "Disk"
sale_price    = "Price"
touch_screen  = "Touch"

[pricing]
tiers = [[5, 50], [999, 200]]
touch_screen_bonus = 25
desktop_penalty = 5

[passmark]
fuzzy_threshold = 75
"""
    )
    cfg = load_config(p)

    assert cfg.snipeit.url == "https://example.invalid"
    assert cfg.snipeit.timeout == 30
    assert cfg.snipeit.max_retries == 5

    assert cfg.custom_fields.cpu_model == "Processor"
    assert cfg.custom_fields.cpu_passmark == "Score"
    assert cfg.custom_fields.touch_screen == "Touch"

    assert [t.max_points for t in cfg.pricing.tiers] == [5, 999]
    assert cfg.pricing.touch_screen_bonus == 25
    assert cfg.pricing.desktop_penalty == 5

    assert cfg.passmark.fuzzy_threshold == 75
    assert cfg.config_path == p


def test_load_config_defaults_when_omitted(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("[snipeit]\nurl = \"https://x.invalid\"\n")
    cfg = load_config(p)

    # Defaults should mirror the bundled DEFAULT_CONFIG_TEMPLATE.
    assert cfg.snipeit.timeout == 10
    assert cfg.snipeit.max_retries == 3
    assert cfg.custom_fields.cpu_model == "CPU"
    assert cfg.custom_fields.ram == "RAM (GB)"
    assert cfg.pricing.touch_screen_bonus == 20
    assert cfg.pricing.desktop_penalty == 3
    assert [t.max_points for t in cfg.pricing.tiers] == [6, 10, 14, 18, 24, 999]
    assert cfg.passmark.fuzzy_threshold == 80


def test_load_config_invalid_toml_raises_value_error(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text('[unterminated\n')
    with pytest.raises(ValueError, match=r"Could not load config\.toml"):
        load_config(p)


def test_load_config_missing_file_raises_value_error(tmp_path: Path) -> None:
    p = tmp_path / "does-not-exist.toml"
    with pytest.raises(ValueError, match=r"Could not load config\.toml"):
        load_config(p)


def test_init_success_writes_human_output_to_stdout(
    runner: CliRunner, reset_state, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("INVENTORY_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.stderr
    assert "Config written to:" in result.stdout
    assert result.stderr == ""
    assert (tmp_path / "inventory" / "config.toml").exists()


def test_configure_logging_is_idempotent() -> None:
    logger = logging.getLogger("snipeit")
    original_handlers = list(logger.handlers)
    try:
        logger.handlers = [
            h for h in logger.handlers if not getattr(h, "_inventory_cli_handler", False)
        ]

        _configure_logging(1)
        _configure_logging(1)

        cli_handlers = [
            h for h in logger.handlers if getattr(h, "_inventory_cli_handler", False)
        ]
        assert len(cli_handlers) == 1
    finally:
        logger.handlers = original_handlers
