"""
inventory.commands._lookup
==========================
Annotated typer-option aliases for parameters that repeat across many
subcommands. Using ``Annotated[type, typer.Option(...)]`` lets us declare
each option once and reuse the alias as the parameter annotation, keeping
the per-command signatures focused on what's actually distinctive.
"""

from __future__ import annotations

from typing import Annotated

import typer

# ── Asset lookup flags (--id / --tag / --serial) ─────────────────────────────
# These three are accepted (mutually exclusive) on every command that targets
# a single asset: get, update, price, label, files {list,upload,download,delete}.
AssetID = Annotated[
    int | None,
    typer.Option("--id", help="Asset ID."),
]
AssetTag = Annotated[
    str | None,
    typer.Option("--tag", help="Asset tag."),
]
AssetSerial = Annotated[
    str | None,
    typer.Option("--serial", help="Serial number."),
]


# ── Model lookup flags (--id / --name) ───────────────────────────────────────
# Used by `models {get,update,delete}` to target a single model.
ModelID = Annotated[
    int | None,
    typer.Option("--id", help="Model ID."),
]
ModelName = Annotated[
    str | None,
    typer.Option("--name", help="Model name."),
]
