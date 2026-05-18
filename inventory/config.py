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
# HTTP request timeout in seconds (per-request). Optional.
# timeout = 10
# Maximum retry attempts for transient errors (429/5xx). Optional.
# max_retries = 3

[custom_fields]
# Maps CLI argument names -> Snipe-IT custom field display labels.
# These must match the label shown in the Snipe-IT UI exactly.
cpu_model     = "CPU"
cpu_passmark  = "CPU PassMark Score"
ram           = "RAM (GB)"
storage       = "Storage (GB)"
sale_price    = "Sale Price"
touch_screen  = "Touchscreen"

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
    timeout: int = 10
    max_retries: int = 3


@dataclass
class CustomFieldsConfig:
    cpu_model: str = "CPU"
    cpu_passmark: str = "CPU PassMark Score"
    ram: str = "RAM (GB)"
    storage: str = "Storage (GB)"
    sale_price: str = "Sale Price"
    touch_screen: str = "Touchscreen"


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

    Raises ValueError if an explicit path (--config or INVENTORY_CONFIG) is
    given but the file does not exist — the caller specified a path and
    getting a silent miss would produce a confusing follow-on error.

    Returns None only when no config is found via the implicit XDG fallback.
    """
    # Priority 1: explicit CLI flag
    if cli_flag:
        p = Path(cli_flag).expanduser().resolve()
        if p.is_file():
            return p
        raise ValueError(f"Config file not found: {p}")

    # Priority 2: environment variable
    env_path = os.environ.get("INVENTORY_CONFIG")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if p.is_file():
            return p
        raise ValueError(f"Config file not found (INVENTORY_CONFIG={env_path}): {p}")

    # Priority 3: XDG config dir — optional, absence is not an error
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
    # Validate prices are monotonically non-decreasing after sorting
    for i in range(1, len(tiers)):
        if tiers[i].price < tiers[i - 1].price:
            raise ValueError(
                f"Pricing tier prices must be non-decreasing, but tier "
                f"(max_points={tiers[i].max_points}, price={tiers[i].price}) "
                f"is less than previous tier (max_points={tiers[i-1].max_points}, "
                f"price={tiers[i-1].price})"
            )
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
        url=snipeit_raw.get("url", "").rstrip("/") or None,
        timeout=int(snipeit_raw.get("timeout", 10)),
        max_retries=int(snipeit_raw.get("max_retries", 3)),
    )

    defaults = CustomFieldsConfig()
    custom_fields = CustomFieldsConfig(
        cpu_model=custom_fields_raw.get("cpu_model", defaults.cpu_model),
        cpu_passmark=custom_fields_raw.get("cpu_passmark", defaults.cpu_passmark),
        ram=custom_fields_raw.get("ram", defaults.ram),
        storage=custom_fields_raw.get("storage", defaults.storage),
        sale_price=custom_fields_raw.get("sale_price", defaults.sale_price),
        touch_screen=custom_fields_raw.get("touch_screen", defaults.touch_screen),
    )

    tiers_raw = pricing_raw.get(
        "tiers", [[6, 100], [10, 125], [14, 150], [18, 175], [24, 200], [999, 250]]
    )
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
