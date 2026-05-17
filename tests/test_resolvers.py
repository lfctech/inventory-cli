"""Tier 2: tests for ``inventory.core.resolvers`` using pytest-httpx."""

from __future__ import annotations

import re

import pytest
from snipeit import SnipeIT

from inventory.core.resolvers import (
    resolve_category,
    resolve_fieldset,
    resolve_manufacturer,
    resolve_model,
    resolve_status_label,
)

from .conftest import TEST_TOKEN, TEST_URL

pytestmark = pytest.mark.unit


@pytest.fixture
def snipeit_client() -> SnipeIT:
    return SnipeIT(url=TEST_URL, token=TEST_TOKEN, max_retries=0)


def _list_response(rows: list[dict]) -> dict:
    """Build the standard Snipe-IT list envelope."""
    return {"total": len(rows), "rows": rows}


# ── resolve_model ────────────────────────────────────────────────────────────


def test_resolve_model_exact_match(snipeit_client: SnipeIT, httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/models\b"),
        json=_list_response([
            {"id": 42, "name": "Latitude 5440"},
        ]),
    )
    assert resolve_model(snipeit_client, "Latitude 5440") == 42


def test_resolve_model_case_insensitive(snipeit_client: SnipeIT, httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/models\b"),
        json=_list_response([
            {"id": 7, "name": "Latitude 5440"},
        ]),
    )
    # Lowercase query should match the canonical name.
    assert resolve_model(snipeit_client, "latitude 5440") == 7


def test_resolve_model_html_entities_normalised(snipeit_client: SnipeIT, httpx_mock) -> None:
    """Snipe-IT can echo HTML-escaped names; the resolver unescapes them."""
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/models\b"),
        json=_list_response([
            {"id": 9, "name": "AT&amp;T ThinkPad"},
        ]),
    )
    assert resolve_model(snipeit_client, "AT&T ThinkPad") == 9


def test_resolve_model_ambiguous_match_raises(snipeit_client: SnipeIT, httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/models\b"),
        json=_list_response([
            {"id": 1, "name": "Latitude 5440"},
            {"id": 2, "name": "Latitude 5440"},
        ]),
    )
    with pytest.raises(ValueError, match="Multiple"):
        resolve_model(snipeit_client, "Latitude 5440")


def test_resolve_model_no_match_lists_available(snipeit_client: SnipeIT, httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/models\b"),
        json=_list_response([
            {"id": 1, "name": "Latitude 5430"},
            {"id": 2, "name": "Latitude 7440"},
        ]),
    )
    with pytest.raises(ValueError) as exc:
        resolve_model(snipeit_client, "Latitude 5440")
    msg = str(exc.value)
    assert "No model found" in msg
    # The error includes the available models so operators can copy/paste.
    assert "Latitude 5430" in msg
    assert "Latitude 7440" in msg


def test_resolve_model_skips_id_when_match_has_no_id(snipeit_client: SnipeIT, httpx_mock) -> None:
    """Defensive: an exact match with no id should produce a clear error
    rather than ``int(None)``."""
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://snipe\.example\.test/api/v1/models\b"),
        json=_list_response([
            {"id": None, "name": "Latitude 5440"},
        ]),
    )
    with pytest.raises(ValueError, match="has no ID"):
        resolve_model(snipeit_client, "Latitude 5440")


# ── resolve_status_label / resolve_category / resolve_manufacturer / resolve_fieldset ──


@pytest.mark.parametrize(
    ("func", "endpoint"),
    [
        (resolve_status_label, "statuslabels"),
        (resolve_category, "categories"),
        (resolve_manufacturer, "manufacturers"),
        (resolve_fieldset, "fieldsets"),
    ],
)
def test_resolve_other_endpoints_smoke(snipeit_client: SnipeIT, httpx_mock, func, endpoint: str) -> None:
    """Each resolver hits the right Snipe-IT endpoint and returns the ID."""
    httpx_mock.add_response(
        method="GET",
        url=re.compile(rf"^https://snipe\.example\.test/api/v1/{endpoint}\b"),
        json=_list_response([{"id": 11, "name": "Refurbished"}]),
    )
    assert func(snipeit_client, "Refurbished") == 11
