"""
inventory.core.resolvers
========================
Name → ID resolution for Snipe-IT resources (models, status labels, categories, manufacturers).
Pure API lookup — no CLI coupling.
"""

from __future__ import annotations

from html import unescape
from typing import Any

from snipeit import SnipeIT


def _norm(s: Any) -> str:
    """Unescape HTML entities and strip whitespace."""
    return unescape((str(s) if s is not None else "").strip())


def _resolve_by_name(endpoint: Any, name: str, entity_type: str) -> int:
    """
    Generic name → ID resolver for any Snipe-IT list endpoint.

    Performs an exact-match (case-insensitive) against the ``name`` field of
    every result returned by ``endpoint.list_all(search=name)``.

    Raises ``ValueError`` if zero or multiple matches are found.
    """
    name_n = _norm(name)
    results = list(endpoint.list_all(search=name_n))
    matches = [
        r for r in results
        if _norm(getattr(r, "name", "")).lower() == name_n.lower()
    ]

    if len(matches) == 1:
        match_id = matches[0].id
        if match_id is None:
            raise ValueError(f'{entity_type} "{name_n}" has no ID')
        return int(match_id)

    if len(matches) > 1:
        names = [_norm(getattr(r, "name", "")) for r in matches]
        raise ValueError(
            f'Multiple {entity_type.lower()}s match "{name_n}": {", ".join(names)}'
        )

    all_names = [_norm(getattr(r, "name", "")) for r in results]
    available = "\n  - ".join(all_names[:20]) if all_names else "(none found)"
    raise ValueError(
        f'No {entity_type.lower()} found matching "{name_n}". '
        f"Available {entity_type.lower()}s:\n  - {available}"
    )


def resolve_model(client: SnipeIT, name: str) -> int:
    """
    Resolve a model name to its Snipe-IT ID.

    Raises ValueError if no exact match or multiple ambiguous matches.
    """
    return _resolve_by_name(client.models, name, "Model")


def resolve_status_label(client: SnipeIT, name: str) -> int:
    """
    Resolve a status label name to its Snipe-IT ID.

    Raises ValueError if no exact match or multiple ambiguous matches.
    """
    return _resolve_by_name(client.status_labels, name, "Status label")


def resolve_category(client: SnipeIT, name: str) -> int:
    """
    Resolve a category name to its Snipe-IT ID.

    Raises ValueError if no exact match or multiple ambiguous matches.
    """
    return _resolve_by_name(client.categories, name, "Category")


def resolve_manufacturer(client: SnipeIT, name: str) -> int:
    """
    Resolve a manufacturer name to its Snipe-IT ID.

    Raises ValueError if no exact match or multiple ambiguous matches.
    """
    return _resolve_by_name(client.manufacturers, name, "Manufacturer")


def resolve_fieldset(client: SnipeIT, name: str) -> int:
    """
    Resolve a fieldset name to its Snipe-IT ID.

    Raises ValueError if no exact match or multiple ambiguous matches.
    """
    return _resolve_by_name(client.fieldsets, name, "Fieldset")
