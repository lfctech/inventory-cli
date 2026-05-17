"""Tier 1: pure-function tests for ``inventory.core.pricing``."""

from __future__ import annotations

import pytest

from inventory.config import PricingConfig, PricingTier
from inventory.core.pricing import (
    calculate_price,
    cpu_points,
    is_desktop_category,
    price_from_points,
    ram_points,
    storage_points,
)

pytestmark = pytest.mark.unit


# ── CPU points (round PassMark / 1000) ───────────────────────────────────────


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, 0),
        (499, 0),
        (500, 0),  # banker's rounding: 0.5 → 0
        (501, 1),
        (1000, 1),
        (1499, 1),
        (1500, 2),  # 1.5 → 2
        (6400, 6),
        (10500, 10),  # 10.5 → 10 (banker's rounding)
        (10501, 11),
        (24000, 24),
    ],
)
def test_cpu_points(score: int, expected: int) -> None:
    assert cpu_points(score) == expected


# ── RAM points ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ram", "expected"),
    [
        (0, 0),
        (4, 0),
        (8, 0),
        (15.999, 0),
        (16, 3),
        (24, 3),
        (31.999, 3),
        (32, 10),
        (64, 10),
        (128, 10),
    ],
)
def test_ram_points(ram: float, expected: int) -> None:
    assert ram_points(ram) == expected


# ── Storage points ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("storage", "expected"),
    [
        (0, 0),
        (128, 0),
        (255.999, 0),
        (256, 2),
        (480, 2),
        (511.999, 2),
        (512, 4),
        (1024, 4),
        (2048, 4),
    ],
)
def test_storage_points(storage: float, expected: int) -> None:
    assert storage_points(storage) == expected


# ── is_desktop_category ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Desktop", True),
        ("Desktops", True),
        ("DESKTOP", True),
        ("desktop pc", True),
        ("Refurb Desktops", True),
        ("Laptop", False),
        ("Laptops", False),
        ("All-in-One", False),
        ("Tablet", False),
        ("", False),
        ("MyDesktopReplacement", False),  # word boundary
    ],
)
def test_is_desktop_category(name: str, expected: bool) -> None:
    assert is_desktop_category(name) is expected


# ── price_from_points (tier mapping) ─────────────────────────────────────────


def _default_tiers() -> list[PricingTier]:
    """The same tier table as the bundled default config."""
    return [
        PricingTier(6, 100),
        PricingTier(10, 125),
        PricingTier(14, 150),
        PricingTier(18, 175),
        PricingTier(24, 200),
        PricingTier(999, 250),
    ]


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        (-5, 100),  # negative still hits first tier
        (0, 100),
        (6, 100),  # boundary
        (7, 125),
        (10, 125),  # boundary
        (11, 150),
        (14, 150),  # boundary
        (15, 175),
        (18, 175),  # boundary
        (19, 200),
        (24, 200),  # boundary
        (25, 250),
        (999, 250),
        (10_000, 250),  # falls through to last tier
    ],
)
def test_price_from_points_default_tiers(points: int, expected: int) -> None:
    assert price_from_points(points, _default_tiers()) == expected


def test_price_from_points_empty_tiers_returns_floor() -> None:
    """Empty tier list should fall through to the documented $100 floor."""
    assert price_from_points(50, []) == 100


def test_price_from_points_unsorted_tiers_does_not_raise() -> None:
    """price_from_points must still produce a deterministic answer even if a
    caller passes unsorted tiers (config._parse_tiers sorts on the way in,
    but defence-in-depth keeps the function honest)."""
    tiers = [PricingTier(999, 250), PricingTier(6, 100), PricingTier(14, 150)]
    # First-match-wins behaviour at runtime — caller is responsible for
    # ordering. Just assert it returns *some* tier price, not a crash.
    assert price_from_points(7, tiers) in {100, 150, 250}


# ── calculate_price (full breakdown) ─────────────────────────────────────────


def _config(**overrides) -> PricingConfig:
    cfg = PricingConfig(
        tiers=_default_tiers(),
        touch_screen_bonus=20,
        desktop_penalty=3,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def test_calculate_price_typical_laptop() -> None:
    # i5-class laptop, 16GB / 512GB, no touch.
    bd = calculate_price(
        passmark_score=8000, ram=16, storage=512, config=_config()
    )
    # 8 + 3 + 4 = 15 points → $175 tier, no touch.
    assert bd.cpu_points == 8
    assert bd.ram_points == 3
    assert bd.storage_points == 4
    assert bd.desktop_adjustment == 0
    assert bd.total_points == 15
    assert bd.base_price == 175
    assert bd.final_price == 175
    assert bd.has_touch is False
    assert bd.touch_bonus == 0


def test_calculate_price_high_end_laptop_with_touch() -> None:
    bd = calculate_price(
        passmark_score=24_000,
        ram=32,
        storage=1024,
        config=_config(),
        has_touch=True,
    )
    # 24 + 10 + 4 = 38 → $250 tier, +$20 touch bonus.
    assert bd.total_points == 38
    assert bd.base_price == 250
    assert bd.touch_bonus == 20
    assert bd.final_price == 270


def test_calculate_price_desktop_penalty_applied() -> None:
    bd = calculate_price(
        passmark_score=8000,
        ram=16,
        storage=512,
        config=_config(),
        is_desktop=True,
    )
    # Same hardware as typical_laptop test (15 points) minus 3 penalty = 12 → $150.
    assert bd.desktop_adjustment == -3
    assert bd.total_points == 12
    assert bd.base_price == 150
    assert bd.final_price == 150


def test_calculate_price_floor_low_spec() -> None:
    bd = calculate_price(
        passmark_score=2500,
        ram=4,
        storage=128,
        config=_config(),
    )
    # 2 + 0 + 0 = 2 → $100 floor.
    assert bd.total_points == 2
    assert bd.base_price == 100
    assert bd.final_price == 100


def test_calculate_price_breakdown_as_dict_round_trip() -> None:
    bd = calculate_price(
        passmark_score=15_000,
        ram=32,
        storage=512,
        config=_config(),
        is_desktop=True,
        has_touch=True,
    )
    d = bd.as_dict()
    # Spot-check critical keys are present with correct types and values.
    assert d["passmark_score"] == 15_000
    assert d["cpu_points"] == 15
    assert d["is_desktop"] is True
    assert d["has_touch"] is True
    assert d["touch_bonus_dollars"] == 20  # public-facing key name
    assert d["final_price"] == bd.final_price


def test_calculate_price_custom_tiers_override() -> None:
    """A non-default tier table is used as configured (no hardcoded prices)."""
    cfg = _config(tiers=[PricingTier(5, 50), PricingTier(999, 200)])
    bd = calculate_price(
        passmark_score=5500,  # 6 points → exceeds 5, falls to second tier
        ram=8,
        storage=128,
        config=cfg,
    )
    assert bd.base_price == 200
    assert bd.final_price == 200


def test_calculate_price_negative_total_clamps_via_first_tier() -> None:
    """Heavily-penalised low-spec desktop still maps to a price (no crash)."""
    cfg = _config(desktop_penalty=20)  # absurd penalty
    bd = calculate_price(
        passmark_score=500,
        ram=0,
        storage=0,
        config=cfg,
        is_desktop=True,
    )
    # 0 + 0 + 0 - 20 = -20 → still hits first tier (100).
    assert bd.total_points == -20
    assert bd.base_price == 100
    assert bd.final_price == 100
