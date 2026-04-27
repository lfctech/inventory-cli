"""
inventory.core.passmark
=======================
PassMark CPU score lookup against the bundled PassmarkCPUList.csv.
Loaded via importlib.resources so it works with both uv run and uvx.

Ported from inventory-resources/inventory/passmark.py — uses rapidfuzz
instead of thefuzz for better performance and no C-extension issues.
"""

from __future__ import annotations

import csv
import functools
import importlib.resources
import logging
import re
from io import StringIO
from typing import TypedDict

from rapidfuzz import fuzz

log = logging.getLogger(__name__)


class PassmarkResult(TypedDict):
    score: int
    matched_cpu: str
    confidence: int


# ── CPU name cleaning ─────────────────────────────────────────────────────────

_NOISE_PATTERNS = [
    r"\(TM\)",
    r"\(R\)",
    r"\bCPU\b",
    r"with Radeon Graphics",
    r"with Radeon Vega Mobile Gfx",
    r"@\s*[\d.]+\s*GHz",
    r"\d+-Core Processor",
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)


def clean_cpu_name(name: str) -> str:
    """Strip known noise tokens from a CPU name before fuzzy comparison."""
    s = _NOISE_RE.sub("", name)
    return re.sub(r"\s+", " ", s).strip()


# ── Model ID extraction ──────────────────────────────────────────────────────

_MODEL_ID_PATTERNS = [
    re.compile(r"(i[3579]-\w+)", re.IGNORECASE),
    re.compile(r"(Ryzen\s+\d\s+\w+)", re.IGNORECASE),
    re.compile(r"(Xeon\s+\w[\w-]+)", re.IGNORECASE),
]


def extract_model_id(cpu_name: str) -> str | None:
    """Extract a short model identifier used for substring filtering."""
    for pattern in _MODEL_ID_PATTERNS:
        m = pattern.search(cpu_name)
        if m:
            return m.group(1)
    return None


# ── CSV loading ──────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _load_bundled_csv() -> tuple[dict, ...]:
    """Load the bundled PassmarkCPUList.csv from package data (cached)."""
    ref = importlib.resources.files("inventory.data").joinpath("passmark.csv")
    text = ref.read_text(encoding="utf-8")
    return tuple(csv.DictReader(StringIO(text)))


def _parse_score(row: dict) -> int | None:
    raw = str(row.get("CPU BenchMark", "") or "").replace(",", "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


# ── Main lookup ──────────────────────────────────────────────────────────────

def lookup_csv(cpu_name: str) -> PassmarkResult | None:
    """
    Return the best PassMark result for ``cpu_name`` from the bundled CSV,
    or ``None`` if the CSV contains no scoreable rows.

    The returned ``confidence`` field is 0-100.
    """
    try:
        rows = _load_bundled_csv()
    except Exception as exc:
        log.warning("Failed to load bundled PassMark CSV: %s", exc)
        return None

    clean = clean_cpu_name(cpu_name)
    log.info("CPU name (cleaned for lookup): %s", clean)

    model_id = extract_model_id(cpu_name)

    # Phase 1: model-ID substring match
    if model_id:
        log.info("Extracted model identifier: %s", model_id)
        candidates = []
        pattern = re.compile(re.escape(model_id), re.IGNORECASE)
        for row in rows:
            csv_cpu = str(row.get("CPU", "") or "").strip()
            if not csv_cpu or not pattern.search(csv_cpu):
                continue
            score = _parse_score(row)
            if score is not None:
                candidates.append({"cpu": csv_cpu, "score": score})

        if candidates:
            if len(candidates) == 1:
                log.info("Single candidate for '%s' — computing real confidence.", model_id)
            else:
                log.info(
                    "%d candidates for '%s' — picking best by fuzzy match.",
                    len(candidates), model_id,
                )
            best = max(
                candidates,
                key=lambda c: fuzz.ratio(clean.lower(), c["cpu"].lower()),
            )
            confidence = int(fuzz.ratio(clean.lower(), best["cpu"].lower()))
            return PassmarkResult(
                score=best["score"],
                matched_cpu=best["cpu"],
                confidence=confidence,
            )

        log.info(
            "No exact model match for '%s' — falling back to full fuzzy search.",
            model_id,
        )
    else:
        log.info("Could not extract model identifier — using full fuzzy search.")

    # Phase 2: full fuzzy search
    best_score = -1
    best_confidence = 0
    best_cpu = ""

    for row in rows:
        csv_cpu = str(row.get("CPU", "") or "").strip()
        if not csv_cpu:
            continue
        ratio = int(fuzz.ratio(clean.lower(), csv_cpu.lower()))
        if ratio > best_confidence:
            score = _parse_score(row)
            if score is not None:
                best_confidence = ratio
                best_score = score
                best_cpu = csv_cpu

    if best_score < 0:
        log.warning("No valid rows found in CSV.")
        return None

    return PassmarkResult(
        score=best_score, matched_cpu=best_cpu, confidence=best_confidence,
    )


def get_average_score() -> int:
    """Return the mean CPU BenchMark score across all valid rows in the bundled CSV."""
    try:
        rows = _load_bundled_csv()
    except Exception:
        return 0

    scores = [s for row in rows if (s := _parse_score(row)) is not None]
    if not scores:
        return 0
    return int(sum(scores) / len(scores))
