"""Regression tests for the DebugOS PassMark matcher port."""

from __future__ import annotations

import pytest

from inventory.core import passmark
from inventory.core.passmark import lookup_csv

pytestmark = pytest.mark.unit


def test_procman_i5_8250u_is_a_unique_exact_match() -> None:
    assert lookup_csv("Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz") == {
        "matched_cpu": "Intel Core i5-8250U @ 1.60GHz",
        "score": 5866,
        "confidence": 100,
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12th Gen Intel(R) Core(TM) i7-1265U", "intel core i7 1265u"),
        ("AMD Ryzen 5 5600X 6-Core Processor", "amd ryzen 5 5600x"),
        ("AMD Ryzen 7 PRO 4750U with Radeon Graphics", "amd ryzen 7 pro 4750u"),
        ("Intel(R) Xeon(R) CPU E5-2670 v2 @ 2.50GHz", "intel xeon e5 2670 v2 2 50ghz"),
    ],
)
def test_normalization_keeps_model_distinctions(raw: str, expected: str) -> None:
    assert passmark._normalize(raw) == expected


@pytest.fixture
def sample_cpus(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (
        ("Intel Core i5-10400 @ 2.90GHz", 12000),
        ("Intel Core i5-10400F @ 2.90GHz", 12100),
        ("Intel Core i5-10400F @ 2.90GHz CPU", 12090),
        ("AMD Ryzen 5 5600", 19000),
        ("AMD Ryzen 5 5600X", 20000),
        ("Dual AMD Ryzen 5 5600X", 30000),
    )
    monkeypatch.setattr(
        passmark,
        "_load_candidates",
        lambda: tuple(
            passmark._Candidate.from_row(name, score) for name, score in rows
        ),
    )


def test_exact_match_does_not_confuse_suffix_variants(sample_cpus: None) -> None:
    result = lookup_csv("Intel(R) Core i5-10400 CPU @ 2.90GHz")
    assert result is not None
    assert result["matched_cpu"] == "Intel Core i5-10400 @ 2.90GHz"
    assert result["confidence"] == 100


def test_duplicate_exact_matches_require_review(sample_cpus: None) -> None:
    result = lookup_csv("Intel Core i5-10400F @ 2.90GHz")
    assert result is not None
    assert result["confidence"] == 50


def test_exact_cpu_keeps_suffix_and_socket_count(sample_cpus: None) -> None:
    result = lookup_csv("AMD Ryzen 5 5600X 6-Core Processor")
    assert result == {"matched_cpu": "AMD Ryzen 5 5600X", "score": 20000, "confidence": 100}


def test_ambiguous_family_is_not_exact(sample_cpus: None) -> None:
    result = lookup_csv("Intel Core i5")
    assert result is not None
    assert result["confidence"] < 100


def test_tied_fuzzy_matches_score_lower() -> None:
    assert passmark._confidence(90, 90, 2) == 45
    assert passmark._confidence(90, 89, 1) < passmark._confidence(90, 75, 1)
    assert passmark._confidence(75, 20, 1) <= 40


def test_empty_cpu_does_not_suggest_a_match() -> None:
    assert lookup_csv(" CPU (TM) ") is None


def test_matching_keeps_non_latin_model_names() -> None:
    exact = lookup_csv("天玑900")
    assert exact is not None and exact["confidence"] == 100
    for query in ("赛扬900", "900"):
        result = lookup_csv(query)
        assert result is None or result["confidence"] < 100


@pytest.mark.parametrize("query", ["Intel Core i5-10400", "Intel Core i5-10400 @ 3.00GHz"])
def test_clock_discrepancy_prefers_correct_sku_and_requires_confirmation(query: str) -> None:
    result = lookup_csv(query)
    assert result is not None
    assert result["matched_cpu"] == "Intel Core i5-10400 @ 2.90GHz"
    assert result["confidence"] < 100


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("AMD Ryzen 5 3500U with Radeon(TM) Vega Mobile Gfx", "AMD Ryzen 5 3500U"),
        ("AMD Ryzen 5 PRO 2400GE w/ Radeon Vega Graphics", "AMD Ryzen 5 PRO 2400GE"),
        ("AMD Ryzen 3 PRO 3200GE w/ Radeon Vega Graphics", "AMD Ryzen 3 PRO 3200GE"),
        ("AMD A6-9225 RADEON R4, 5 COMPUTE CORES 2C+3G", "AMD A6-9225"),
        ("AMD Ryzen 7 7840U w/ Radeon 780M Graphics", "AMD Ryzen 7 7840U"),
        ("AMD Ryzen 5 7640U w/ Radeon 760M Graphics", "AMD Ryzen 5 7640U"),
        ("AMD A8-6410 APU with AMD Radeon R5 Graphics", "AMD A8-6410 APU"),
        ("AMD A10-5745M APU with Radeon(tm) HD Graphics", "AMD A10-5745M APU"),
    ],
)
def test_graphics_descriptions_preserve_the_exact_cpu(query: str, expected: str) -> None:
    result = lookup_csv(query)
    assert result is not None
    assert result["matched_cpu"] == expected
    assert result["confidence"] == 100


def test_virtual_machine_type_has_no_cpu_suggestion() -> None:
    assert lookup_csv(" ".join(["pc-q35-11.0"] * 4)) is None


@pytest.mark.parametrize("query", [
    "Intel Core i5-10400K @ 2.90GHz", "i5-8250U", "8250U",
    "Intel Core i7-10400", "AMD Ryzen 5 PRO 5600X",
])
def test_missing_model_does_not_offer_a_different_cpu(sample_cpus: None, query: str) -> None:
    assert lookup_csv(query) is None


@pytest.mark.parametrize("query", ["10400F", "Intel Core i5-10400F @ 2900 MHz 6 cores 12 threads"])
def test_partial_name_still_suggests_the_same_model(sample_cpus: None, query: str) -> None:
    result = lookup_csv(query)
    assert result is not None
    assert "i5-10400F" in result["matched_cpu"]
    assert result["confidence"] < 100


@pytest.mark.parametrize("sku", ["7730U", "3700U"])
@pytest.mark.parametrize("missing_pro", [False, True])
def test_missing_ryzen_edition_rejects_its_sibling(monkeypatch, sku: str, missing_pro: bool) -> None:
    standard = f"AMD Ryzen 7 {sku}"
    pro = f"AMD Ryzen 7 PRO {sku}"
    missing, available = (pro, standard) if missing_pro else (standard, pro)
    row = passmark._Candidate.from_row(available, 12000)
    monkeypatch.setattr(passmark, "_load_candidates", lambda: (row,))
    assert lookup_csv(f"{missing} with Radeon Graphics") is None
    assert lookup_csv(available) == {"matched_cpu": available, "score": 12000, "confidence": 100}


def test_xeon_firmware_zero_before_clock_keeps_the_original_sku() -> None:
    result = lookup_csv("Intel(R) Xeon(R) CPU E5-2690 0 @ 2.90GHz")
    assert result == {"matched_cpu": "Intel Xeon E5-2690 @ 2.90GHz", "score": 9740, "confidence": 100}

@pytest.mark.parametrize(("name", "expected"), [
    ("Intel(R) Core(TM)2 Duo CPU E8400 @ 3.00GHz", "Intel Core2 Duo E8400 @ 3.00GHz"),
    ("Intel Core 2 Quad Q6600 @ 2.40GHz", "Intel Core2 Quad Q6600 @ 2.40GHz"),
])
def test_core2_family_spelling(name: str, expected: str) -> None:
    result = lookup_csv(name)
    assert result is not None and result["matched_cpu"] == expected and result["confidence"] == 100


@pytest.mark.parametrize(("name", "score"), [("Rockchip RK3328", 319), ("rockchip,rk3328", 761)])
def test_literal_spelling_selects_which_duplicate_to_confirm(name: str, score: int) -> None:
    assert lookup_csv(name) == {"matched_cpu": name, "score": score, "confidence": 50}
