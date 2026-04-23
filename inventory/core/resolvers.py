"""
inventory.core.resolvers
========================
Name → ID resolution for Snipe-IT models and status labels.
Pure API lookup — no CLI coupling.
"""

from __future__ import annotations

from html import unescape
from typing import Any

from snipeit import SnipeIT


def _norm(s: Any) -> str:
    """Unescape HTML entities and strip whitespace."""
    return unescape((str(s) if s is not None else "").strip())


def resolve_model(client: SnipeIT, name: str) -> int:
    """
    Resolve a model name to its Snipe-IT ID.

    Raises ValueError if no exact match or multiple ambiguous matches.
    """
    name_n = _norm(name)
    results = client.models.list(search=name_n)
    matches = [
        m for m in results
        if _norm(getattr(m, "name", "")).lower() == name_n.lower()
    ]

    if len(matches) == 1:
        return matches[0].id

    if len(matches) > 1:
        names = [_norm(getattr(m, "name", "")) for m in matches]
        raise ValueError(
            f'Multiple models match "{name_n}": {", ".join(names)}'
        )

    # No match — list available models for a helpful error
    all_names = [_norm(getattr(m, "name", "")) for m in results]
    available = "\n  - ".join(all_names[:20]) if all_names else "(none found)"
    raise ValueError(
        f'No model found matching "{name_n}". Available models:\n  - {available}'
    )


def resolve_status_label(client: SnipeIT, name: str) -> int:
    """
    Resolve a status label name to its Snipe-IT ID.

    Raises ValueError if no exact match or multiple ambiguous matches.
    """
    name_n = _norm(name)
    results = client.status_labels.list(search=name_n)
    matches = [
        s for s in results
        if _norm(getattr(s, "name", "")).lower() == name_n.lower()
    ]

    if len(matches) == 1:
        return matches[0].id

    if len(matches) > 1:
        names = [_norm(getattr(s, "name", "")) for s in matches]
        raise ValueError(
            f'Multiple status labels match "{name_n}": {", ".join(names)}'
        )

    all_names = [_norm(getattr(s, "name", "")) for s in results]
    available = "\n  - ".join(all_names[:20]) if all_names else "(none found)"
    raise ValueError(
        f'No status label found matching "{name_n}". Available status labels:\n  - {available}'
    )
