"""Tier 1: pure-function tests for ``inventory.core.passmark``."""

from __future__ import annotations

import pytest

from inventory.core.passmark import (
    clean_cpu_name,
    extract_model_id,
    get_average_score,
    lookup_csv,
)

pytestmark = pytest.mark.unit


# ── clean_cpu_name ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Trademark and registration noise
        ("Intel(R) Core(TM) i7-1165G7", "Intel Core i7-1165G7"),
        ("Intel(R) Xeon(R) CPU E5-2670 0", "Intel Xeon E5-2670 0"),
        # Frequency suffix
        ("AMD Ryzen 5 5600X @ 3.70 GHz", "AMD Ryzen 5 5600X"),
        ("Intel Core i5-8500 @ 3.0GHz", "Intel Core i5-8500"),
        # Multi-core suffix
        ("AMD Ryzen 9 5900X 12-Core Processor", "AMD Ryzen 9 5900X"),
        ("Intel Core i7-9700K 8-Core Processor @ 3.6GHz", "Intel Core i7-9700K"),
        # Radeon graphics tail (a real-world Snipe-IT input)
        ("AMD Ryzen 5 5600G with Radeon Graphics", "AMD Ryzen 5 5600G"),
        ("AMD Athlon Silver 3050U with Radeon Vega Mobile Gfx", "AMD Athlon Silver 3050U"),
        # Already-clean names round-trip unchanged.
        ("Intel Core i5-10400", "Intel Core i5-10400"),
        # Whitespace normalisation
        ("Intel  Core   i5-10400", "Intel Core i5-10400"),
    ],
)
def test_clean_cpu_name(raw: str, expected: str) -> None:
    assert clean_cpu_name(raw) == expected


def test_clean_cpu_name_empty() -> None:
    assert clean_cpu_name("") == ""


# ── extract_model_id ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Intel Core i-series
        ("Intel(R) Core(TM) i5-10400 @ 2.90GHz", "i5-10400"),
        ("Intel Core i7-1165G7", "i7-1165G7"),
        ("intel core i9-9900K", "i9-9900K"),
        # i3-class
        ("Intel Core i3-1115G4", "i3-1115G4"),
        # AMD Ryzen
        ("AMD Ryzen 5 5600X 6-Core Processor", "Ryzen 5 5600X"),
        ("AMD Ryzen 7 PRO 4750U", "Ryzen 7 PRO"),  # greedy match captures next token
        # Xeon — extract_model_id runs on the raw input *before* clean_cpu_name,
        # so the regex needs whitespace after "Xeon" (no parens).
        ("Intel Xeon E5-2670", "Xeon E5-2670"),
        ("Intel Xeon Silver 4210R", "Xeon Silver"),
        # The ``(R)`` form does NOT match — extract_model_id is intentionally
        # cheap (substring filter); the cleaned-name fuzzy fallback handles it.
        ("Intel(R) Xeon(R) E5-2670", None),
        # Patterns that don't match return None
        ("Apple M1 Pro", None),
        ("Snapdragon 8cx Gen 3", None),
        ("", None),
    ],
)
def test_extract_model_id(raw: str, expected: str | None) -> None:
    assert extract_model_id(raw) == expected


# ── lookup_csv (against bundled CSV) ─────────────────────────────────────────
#
# Confidence thresholds below are calibrated against the bundled
# ``inventory/data/passmark.csv``. They are deliberately conservative —
# if the CSV is refreshed and a particular CPU's row drifts, the test
# still passes as long as the fuzzy match remains in the operator-accept
# band. Tighten only when the production fuzzy_threshold is raised.


def test_lookup_csv_high_confidence_intel_match() -> None:
    """A canonical CPU name should resolve in the operator-accept band
    (>= 70%, comfortably above noise) and pick the right family."""
    result = lookup_csv("Intel Core i5-10400")
    assert result is not None
    assert "i5-10400" in result["matched_cpu"]
    assert result["confidence"] >= 70
    assert result["score"] > 0


def test_lookup_csv_dirty_name_via_clean_pipeline() -> None:
    """Real-world dirty input (TM/R/freq) still finds the right CPU,
    just at slightly lower confidence (~78% pre-prompt threshold)."""
    result = lookup_csv("Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz")
    assert result is not None
    assert "i7-8700" in result["matched_cpu"]
    assert result["confidence"] >= 70


def test_lookup_csv_amd_ryzen_match() -> None:
    result = lookup_csv("AMD Ryzen 5 5600X 6-Core Processor")
    assert result is not None
    assert "5600X" in result["matched_cpu"]
    assert result["confidence"] >= 70


def test_lookup_csv_unknown_model_falls_back_to_full_search() -> None:
    """An unrecognised pattern triggers Phase 2 (full fuzzy) and still
    returns *some* result rather than raising."""
    result = lookup_csv("Totally Made Up CPU Model XYZ-9999")
    assert result is not None
    assert isinstance(result["score"], int)
    assert 0 <= result["confidence"] <= 100
    # Confidence will be low — that's the operator-prompt branch's job.
    assert result["confidence"] < 80


def test_lookup_csv_returns_typed_dict_keys() -> None:
    """Spot-check the contract — the calling code reads these three keys."""
    result = lookup_csv("Intel Core i5-10400")
    assert result is not None
    assert set(result.keys()) == {"score", "matched_cpu", "confidence"}


# ── get_average_score ────────────────────────────────────────────────────────


def test_get_average_score_is_positive_and_within_plausible_range() -> None:
    """The bundled CSV must contain enough scoreable rows to produce a
    reasonable average. We don't pin an exact value (the CSV may be
    refreshed) — only that the result is a positive integer in a
    plausible band for consumer CPUs."""
    avg = get_average_score()
    assert isinstance(avg, int)
    assert 1_000 <= avg <= 50_000
