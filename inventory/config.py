"""
inventory.config
================
Load and validate config.toml with three-tier resolution:
  1. --config CLI flag
  2. INVENTORY_CONFIG environment variable
  3. XDG config dir (~/.config/inventory/config.toml)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Default config template ───────────────────────────────────────────────────

DEFAULT_CONFIG_TEMPLATE = """\
# Default configuration for inventory CLI.
# Generate this file with: inventory init

[snipeit]
# Snipe-IT instance URL. Can also be set via SNIPEIT_URL env var or --url flag.
url = "https://inventory.example.com"

[custom_fields]
# Maps CLI argument names -> Snipe-IT custom field db column names.
# Update these to match your Snipe-IT instance's custom field keys.
cpu_model     = "_snipeit_cpu_model_1"
cpu_passmark  = "_snipeit_cpu_passmark_2"
ram_gb        = "_snipeit_ram_gb_3"
storage_gb    = "_snipeit_storage_gb_4"
sale_price    = "_snipeit_sale_price_5"
touch_screen  = "_snipeit_touch_screen_6"

[pricing]
# Points -> price tier table.
# Each entry is [max_points, price_usd]. First tier where points <= max_points wins.
tiers = [
    [6,  100],
    [10, 125],
    [14, 150],
    [18, 175],
    [24, 200],
    [999, 250],
]
touch_screen_bonus = 20
desktop_penalty    = 3

[passmark]
fuzzy_threshold = 80  # confidence % below which operator is prompted
"""


# ── Parsed config dataclasses ────────────────────────────────────────────────

@dataclass
class SnipeITConfig:
    url: str | None = None


@dataclass
class CustomFieldsConfig:
    cpu_model: str = "_snipeit_cpu_model_1"
    cpu_passmark: str = "_snipeit_cpu_passmark_2"
    ram_gb: str = "_snipeit_ram_gb_3"
    storage_gb: str = "_snipeit_storage_gb_4"
    sale_price: str = "_snipeit_sale_price_5"
    touch_screen: str = "_snipeit_touch_screen_6"


@dataclass
class PricingTier:
    max_points: int
    price: int


@dataclass
class PricingConfig:
    tiers: list[PricingTier] = field(default_factory=list)
    touch_screen_bonus: int = 20
    desktop_penalty: int = 3


@dataclass
class PassmarkConfig:
    fuzzy_threshold: int = 80


@dataclass
class AppConfig:
    snipeit: SnipeITConfig = field(default_factory=SnipeITConfig)
    custom_fields: CustomFieldsConfig = field(default_factory=CustomFieldsConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)
    passmark: PassmarkConfig = field(default_factory=PassmarkConfig)
    config_path: Path | None = None


# ── Resolution ────────────────────────────────────────────────────────────────

def _xdg_config_path() -> Path:
    """Return the XDG config path for inventory."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "inventory" / "config.toml"
    return Path.home() / ".config" / "inventory" / "config.toml"


def resolve_config_path(cli_flag: str | None = None) -> Path | None:
    """
    Resolve the config.toml path using the three-tier fallback chain.
    Returns None if no config file is found.
    """
    # Priority 1: explicit CLI flag
    if cli_flag:
        p = Path(cli_flag).expanduser().resolve()
        if p.is_file():
            return p
        return None

    # Priority 2: environment variable
    env_path = os.environ.get("INVENTORY_CONFIG")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if p.is_file():
            return p
        return None

    # Priority 3: XDG config dir
    xdg = _xdg_config_path()
    if xdg.is_file():
        return xdg

    return None


def _parse_tiers(raw: list[list[int]]) -> list[PricingTier]:
    """Parse [[max_points, price], ...] into PricingTier objects."""
    tiers = []
    for entry in raw:
        if len(entry) != 2:
            raise ValueError(f"Each pricing tier must be [max_points, price], got {entry}")
        tiers.append(PricingTier(max_points=int(entry[0]), price=int(entry[1])))
    # Sort by max_points ascending for correct tier lookup
    tiers.sort(key=lambda t: t.max_points)
    return tiers


def load_config(config_path: Path) -> AppConfig:
    """Load and validate config.toml from the given path."""
    try:
        with open(config_path, "rb") as f:
            raw: dict[str, Any] = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ValueError(f"Could not load config.toml: {exc}") from exc

    snipeit_raw = raw.get("snipeit", {})
    custom_fields_raw = raw.get("custom_fields", {})
    pricing_raw = raw.get("pricing", {})
    passmark_raw = raw.get("passmark", {})

    snipeit = SnipeITConfig(
        url=snipeit_raw.get("url"),
    )

    custom_fields = CustomFieldsConfig(
        cpu_model=custom_fields_raw.get("cpu_model", "_snipeit_cpu_model_1"),
        cpu_passmark=custom_fields_raw.get("cpu_passmark", "_snipeit_cpu_passmark_2"),
        ram_gb=custom_fields_raw.get("ram_gb", "_snipeit_ram_gb_3"),
        storage_gb=custom_fields_raw.get("storage_gb", "_snipeit_storage_gb_4"),
        sale_price=custom_fields_raw.get("sale_price", "_snipeit_sale_price_5"),
        touch_screen=custom_fields_raw.get("touch_screen", "_snipeit_touch_screen_6"),
    )

    tiers_raw = pricing_raw.get("tiers", [[6, 100], [10, 125], [14, 150], [18, 175], [24, 200], [999, 250]])
    pricing = PricingConfig(
        tiers=_parse_tiers(tiers_raw),
        touch_screen_bonus=pricing_raw.get("touch_screen_bonus", 20),
        desktop_penalty=pricing_raw.get("desktop_penalty", 3),
    )

    passmark = PassmarkConfig(
        fuzzy_threshold=passmark_raw.get("fuzzy_threshold", 80),
    )

    return AppConfig(
        snipeit=snipeit,
        custom_fields=custom_fields,
        pricing=pricing,
        passmark=passmark,
        config_path=config_path,
    )
