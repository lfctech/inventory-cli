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
            passmark._Candidate(name, score, norm, frozenset(norm.split()))
            for name, score in rows
            if (norm := passmark._normalize(name))
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


@pytest.mark.parametrize(
    "query", ["Intel Core i5", "Intel Core i5-10400K @ 2.90GHz", "Made Up CPU XYZ-9999"]
)
def test_ambiguous_or_unknown_cpu_is_not_exact(sample_cpus: None, query: str) -> None:
    result = lookup_csv(query)
    assert result is not None
    assert result["confidence"] < 100


def test_tied_fuzzy_matches_score_lower() -> None:
    assert passmark._confidence(90, 90, 2) == 45
    assert passmark._confidence(90, 89, 1) < passmark._confidence(90, 75, 1)
    assert passmark._confidence(75, 20, 1) <= 40


def test_empty_cpu_does_not_suggest_a_match() -> None:
    assert lookup_csv(" CPU (TM) ") is None
