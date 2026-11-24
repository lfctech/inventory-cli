"""Look up PassMark scores with a matcher based on the DebugOS Go implementation.

Ported from lfctech/debugOS-software, cmd/passmark-lookup (PR #11).
RapidFuzz supplies Levenshtein distance; no Go binary is needed.
"""

from __future__ import annotations

import csv
import functools
import importlib.resources
import logging
import re
from io import StringIO
from typing import NamedTuple, TypedDict

from rapidfuzz.distance import Levenshtein

log = logging.getLogger(__name__)

EXACT_CONFIDENCE = 100
_TOKEN_SUBSET_FLOOR = 90
_MIN_SUGGESTION_SCORE = 50  # DebugOS default raw-score cutoff
_ABSOLUTE_FLOOR = 80
_MARGIN_K = 8
_WEAK_MATCH_CAP = 40

_TRADEMARK = re.compile(r"\((?:r|tm)\)", re.IGNORECASE)
_NOISE = re.compile(
    r"\b\d+(?:st|nd|rd|th)\s+gen\s+|\b\d+\s*-\s*core\s+processor\b|\bcpu\b"
    r"|\b(?:with\s+|w/\s*)(?:amd\s+)?radeon\s+"
    r"(?:vega\s+mobile\s+gfx|(?:(?:vega|hd|r\d+)\s+)?graphics)\b"
    r"|\bradeon\s+r\d+,\s*\d+\s+compute\s+cores\s+\d+c\s*\+\s*\d+g\s*$",
    re.IGNORECASE | re.ASCII,
)
_NON_ALNUM = re.compile(r"[\W_]+")
_CLOCK_SUFFIX = re.compile(r"\s*@\s*\d+(?:\.\d+)?\s*[gm]hz\s*$", re.IGNORECASE)


class PassmarkResult(TypedDict):
    score: int
    matched_cpu: str
    confidence: int


class _Candidate(NamedTuple):
    name: str
    score: int
    normalized: str
    tokens: frozenset[str]
    model: str

    @classmethod
    def from_row(cls, name: str, score: int) -> _Candidate:
        normalized = _normalize(name)
        return cls(name, score, normalized, frozenset(normalized.split()), _model(name, normalized))


def _normalize(name: str) -> str:
    # Keep clocks and model suffixes, as DebugOS does.
    unmarked = _TRADEMARK.sub(" ", name)
    return _NON_ALNUM.sub(" ", _NOISE.sub(" ", unmarked).lower()).strip()


def _model(name: str, normalized: str) -> str:
    """Keep the full model designation, omitting only an explicit trailing clock."""
    clock = _CLOCK_SUFFIX.search(name)
    return _normalize(name[:clock.start()]) if clock else normalized


@functools.lru_cache(maxsize=1)
def _load_candidates() -> tuple[_Candidate, ...]:
    ref = importlib.resources.files("inventory.data").joinpath("passmark.csv")
    candidates = []
    for row in csv.DictReader(StringIO(ref.read_text(encoding="utf-8-sig"))):
        name = (row.get("CPU") or "").strip()
        raw = (row.get("CPU BenchMark") or "").replace(",", "").strip()
        try:
            score = int(raw)
        except ValueError:
            continue
        if score > 0:
            candidate = _Candidate.from_row(name, score)
            if candidate.normalized:
                candidates.append(candidate)
    return tuple(candidates)


def _round_ratio(numerator: int, denominator: int) -> int:
    """Round a nonnegative ratio like Go's math.Round, including half ties."""
    return (2 * numerator + denominator) // (2 * denominator)


def _match_score(query: str, tokens: frozenset[str], candidate: _Candidate) -> int:
    size = max(len(query), len(candidate.normalized))
    distance = Levenshtein.distance(query, candidate.normalized)
    score = _round_ratio((size - distance) * 100, size)
    if tokens <= candidate.tokens or candidate.tokens <= tokens:
        score = max(score, _TOKEN_SUBSET_FLOOR)
    return score


def _confidence(best: int, second: int, ties: int) -> int:
    margin = best - max(0, second)
    if margin == 0 and ties > 1:
        return max(1, min(99, _round_ratio(best, ties)))
    factor = _round_ratio(100 * margin, margin + _MARGIN_K)
    confidence = _round_ratio(best * factor, 100)
    return min(confidence, _WEAK_MATCH_CAP if best < _ABSOLUTE_FLOOR else 99)


def lookup_csv(cpu_name: str) -> PassmarkResult | None:
    """Return the best scored row. Only a unique normalized match scores 100."""
    query = _normalize(cpu_name)
    if not query:
        return None
    try:
        candidates = _load_candidates()
    except (OSError, UnicodeError, csv.Error) as exc:
        log.warning("Failed to load bundled PassMark CSV: %s", exc)
        return None
    if not candidates:
        return None

    exact = [candidate for candidate in candidates if candidate.normalized == query]
    if exact:
        best = exact[0]
        confidence = EXACT_CONFIDENCE if len(exact) == 1 else max(1, _round_ratio(100, len(exact)))
    else:
        tokens = frozenset(query.split())
        # A clock discrepancy still needs confirmation. Rank rows for the same
        # model first so a single-digit change to the SKU cannot outrank it.
        model = _model(cpu_name, query)
        same_model = [candidate for candidate in candidates if model and candidate.model == model]
        pool = same_model or candidates
        best = pool[0]
        best_score = second_score = -1
        ties = 0
        for candidate in pool:
            score = _match_score(query, tokens, candidate)
            if score > best_score:
                best, best_score, second_score = candidate, score, best_score
                ties = 1
            elif score == best_score:
                ties += 1
                second_score = best_score
            else:
                second_score = max(second_score, score)
        if not same_model and best_score < _MIN_SUGGESTION_SCORE:
            return None
        confidence = _confidence(best_score, second_score, ties)

    return PassmarkResult(score=best.score, matched_cpu=best.name, confidence=confidence)
