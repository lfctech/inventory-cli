"""inventory CLI — Snipe-IT asset management from the command line."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("inventory-cli")
except PackageNotFoundError:  # pragma: no cover — only hit during local dev w/o install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
