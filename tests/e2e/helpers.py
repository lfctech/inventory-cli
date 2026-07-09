from __future__ import annotations

import json
import re
import uuid
from typing import Any


def id_int(value: Any) -> int:
    return int(getattr(value, "id", value))


def unique_name(prefix: str, run_id: str) -> str:
    return f"inventory-e2e-{prefix}-{run_id}-{uuid.uuid4().hex[:6]}"


def parse_json_output(output: str) -> Any:
    return json.loads(_strip_ansi(output))


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)
