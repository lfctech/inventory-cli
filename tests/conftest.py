"""
Shared pytest fixtures for inventory-cli tests.

Conventions:
* Tests use ``https://snipe.example.test`` as the mocked Snipe-IT base URL
  (RFC 6761 reserved domain — never resolves in real DNS, so a missed mock
  fails fast instead of leaking traffic to a real host).
* CLI tests reset ``inventory.main.state`` between runs so tests stay
  isolated even though the module-level state object is shared.
* Rich's ``Console`` caches its is-terminal detection at module-import
  time, so the per-test ``NO_COLOR`` env doesn't reach already-constructed
  consoles. Tests strip ANSI escapes in assertions via :func:`strip_ansi`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

# Public so individual tests can use the same constant.
TEST_URL = "https://snipe.example.test"
TEST_TOKEN = "fake-token"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI colour/style escapes from ``text``.

    Rich's ``Console`` caches its terminal-detection state at construction
    time. Production CLI invocations behave correctly because Rich correctly
    detects ``sys.stdout`` as a terminal *or* a pipe at that point. Under
    pytest's CliRunner the stdout/stderr swap happens *after* the Console
    has already been built, so the per-test ``NO_COLOR`` env is too late.
    Stripping here keeps assertions stable without coupling production code
    to the test runner.
    """
    return _ANSI_RE.sub("", text)


@pytest.fixture
def runner() -> CliRunner:
    """A typer CliRunner.

    Newer typer/click versions split stderr from stdout by default and no
    longer accept the legacy ``mix_stderr`` kwarg.
    """
    return CliRunner()


@pytest.fixture
def reset_state():
    """Reset ``inventory.main.state`` after each test.

    The CLI uses a module-level mutable state object populated by the root
    typer callback. Without this fixture, leftover ``url`` / ``api_key`` /
    ``config`` values would leak between tests.
    """
    from inventory.main import state

    saved = (state.url, state.api_key, state.config, state.json_output, state.verbose)
    yield
    state.url, state.api_key, state.config, state.json_output, state.verbose = saved


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject SNIPEIT_URL / SNIPEIT_API_KEY for CLI invocations."""
    monkeypatch.setenv("SNIPEIT_URL", TEST_URL)
    monkeypatch.setenv("SNIPEIT_API_KEY", TEST_TOKEN)
    # Ensure tests never inherit the developer's INVENTORY_CONFIG.
    monkeypatch.delenv("INVENTORY_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # Disable Rich's colorisation so JSON output is parseable and stderr
    # substring assertions don't get hit by ANSI escape sequences.
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Write a minimal config.toml and return its path.

    Tests that need a config-loaded run pass ``--config <config_file>`` to
    the CLI invocation. Custom field labels match the values the CLI uses
    in its default ``CustomFieldsConfig``.
    """
    p = tmp_path / "config.toml"
    p.write_text(
        f"""\
[snipeit]
url = "{TEST_URL}"

[custom_fields]
cpu_model     = "CPU"
cpu_passmark  = "CPU PassMark Score"
ram           = "RAM (GB)"
storage       = "Storage (GB)"
sale_price    = "Sale Price"
touch_screen  = "Touchscreen"

[pricing]
tiers = [[6, 100], [10, 125], [14, 150], [18, 175], [24, 200], [999, 250]]
touch_screen_bonus = 20
desktop_penalty    = 3

[passmark]
fuzzy_threshold = 80
"""
    )
    return p


# ── Mock helpers ─────────────────────────────────────────────────────────────


def asset_payload(
    *,
    asset_id: int = 1,
    asset_tag: str = "LFC-1",
    name: str = "Test Asset",
    serial: str = "SN-1",
    model: dict | None = None,
    status_label: dict | None = None,
    category: dict | None = None,
    custom_fields: dict | None = None,
) -> dict:
    """Build a Snipe-IT asset response dict with sensible defaults.

    Custom fields default to a "Refurbishing" set with empty values so the
    asset is fetchable but `set_custom_field` calls work without staging
    state errors. Override per-test by passing ``custom_fields={...}``.
    """
    if custom_fields is None:
        custom_fields = {
            "CPU": {"field": "_snipeit_cpu_1", "value": ""},
            "CPU PassMark Score": {"field": "_snipeit_cpu_passmark_score_2", "value": ""},
            "RAM (GB)": {"field": "_snipeit_ram_gb_3", "value": ""},
            "Storage (GB)": {"field": "_snipeit_storage_gb_4", "value": ""},
            "Sale Price": {"field": "_snipeit_sale_price_5", "value": ""},
            "Touchscreen": {"field": "_snipeit_touchscreen_6", "value": ""},
        }
    return {
        "id": asset_id,
        "asset_tag": asset_tag,
        "name": name,
        "serial": serial,
        "model": model or {"id": 1, "name": "Latitude 5440"},
        "status_label": status_label or {"id": 1, "name": "Refurbished"},
        "category": category or {"id": 1, "name": "Laptops"},
        "custom_fields": custom_fields,
    }
