"""
inventory.core.pricing
======================
Point scoring and price-tier logic.  Pure functions — no I/O, no API calls.
Ported from inventory-resources/inventory/pricing.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import PricingConfig, PricingTier


@dataclass
class PriceBreakdown:
    """Full breakdown of a price calculation for display and audit."""

    passmark_score: int
    cpu_points: int
    ram_gb: float
    ram_points: int
    storage_gb: float
    storage_points: int
    is_desktop: bool
    desktop_adjustment: int
    has_touch: bool
    touch_bonus: int
    total_points: int
    base_price: int
    final_price: int

    def as_dict(self) -> dict:
        return {
            "passmark_score": self.passmark_score,
            "cpu_points": self.cpu_points,
            "ram_gb": self.ram_gb,
            "ram_points": self.ram_points,
            "storage_gb": self.storage_gb,
            "storage_points": self.storage_points,
            "is_desktop": self.is_desktop,
            "desktop_adjustment": self.desktop_adjustment,
            "has_touch": self.has_touch,
            "touch_bonus_dollars": self.touch_bonus,
            "total_points": self.total_points,
            "base_price": self.base_price,
            "final_price": self.final_price,
        }


def cpu_points(passmark_score: int) -> int:
    """Round PassMark / 1000 to get CPU points (e.g. 6,400 → 6)."""
    return round(passmark_score / 1000)


def ram_points(ram_gb: float) -> int:
    if ram_gb >= 32:
        return 10
    if ram_gb >= 16:
        return 3
    return 0


def storage_points(storage_gb: float) -> int:
    if storage_gb >= 512:
        return 4
    if storage_gb >= 256:
        return 2
    return 0


def is_desktop_category(category_name: str) -> bool:
    """Return True if the category name indicates a desktop form factor."""
    return bool(re.search(r"\bdesktops?\b", category_name, re.IGNORECASE))


def price_from_points(total_points: int, tiers: list[PricingTier]) -> int:
    """Map a total point score to a sale price using config-driven tiers."""
    for tier in tiers:
        if total_points <= tier.max_points:
            return tier.price
    # Fallback: return the last tier's price
    return tiers[-1].price if tiers else 100


def calculate_price(
    passmark_score: int,
    ram_gb: float,
    storage_gb: float,
    config: PricingConfig,
    is_desktop: bool = False,
    has_touch: bool = False,
) -> PriceBreakdown:
    """
    Calculate the sale price for a device given its hardware specs.
    Returns a PriceBreakdown with all intermediate values for display.
    """
    c_pts = cpu_points(passmark_score)
    r_pts = ram_points(ram_gb)
    s_pts = storage_points(storage_gb)
    d_adj = -config.desktop_penalty if is_desktop else 0
    total = c_pts + r_pts + s_pts + d_adj
    base = price_from_points(total, config.tiers)
    touch = config.touch_screen_bonus if has_touch else 0
    final = base + touch

    return PriceBreakdown(
        passmark_score=passmark_score,
        cpu_points=c_pts,
        ram_gb=ram_gb,
        ram_points=r_pts,
        storage_gb=storage_gb,
        storage_points=s_pts,
        is_desktop=is_desktop,
        desktop_adjustment=d_adj,
        has_touch=has_touch,
        touch_bonus=touch,
        total_points=total,
        base_price=base,
        final_price=final,
    )
