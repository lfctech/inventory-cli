from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from snipeit import SnipeIT
from typer.testing import CliRunner

from inventory.main import state
from tests.e2e.helpers import id_int

E2E_URL = "http://localhost:8010"


@pytest.fixture(scope="session", autouse=True)
def _configure_e2e_env() -> Iterator[None]:
    root = Path(__file__).resolve().parents[2]
    api_key_file = root / "docker" / "api_key.txt"

    if not api_key_file.exists():
        pytest.skip(
            "E2E tests require docker/api_key.txt. "
            "Run 'make test-e2e' to start local Snipe-IT and generate a token."
        )

    token = api_key_file.read_text().strip()
    if not token:
        pytest.skip(
            "E2E tests require a non-empty docker/api_key.txt. "
            "Run 'make test-e2e' to start local Snipe-IT and generate a token."
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SNIPEIT_URL", E2E_URL)
        mp.setenv("SNIPEIT_API_KEY", token)
        mp.delenv("INVENTORY_CONFIG", raising=False)
        mp.delenv("XDG_CONFIG_HOME", raising=False)
        mp.setenv("NO_COLOR", "1")
        mp.setenv("TERM", "dumb")
        yield


@pytest.fixture(scope="session")
def run_id() -> str:
    return time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]


@pytest.fixture(scope="session")
def real_snipeit_client() -> Iterator[SnipeIT]:
    token = (
        Path(__file__).resolve().parents[2].joinpath("docker", "api_key.txt").read_text().strip()
    )
    client = SnipeIT(
        url=E2E_URL,
        token=token,
        max_retries=5,
        retry_allowed_methods={"HEAD", "GET", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"},
    )
    yield client
    client.close()


@pytest.fixture(scope="session")
def base(real_snipeit_client: SnipeIT, run_id: str) -> Iterator[dict[str, Any]]:
    client = real_snipeit_client
    prefix = f"inventory-e2e-{run_id}"

    manufacturer = client.manufacturers.create(name=f"{prefix}-manufacturer")
    category = client.categories.create(name=f"{prefix}-category", category_type="asset")
    status = client.status_labels.create(name=f"{prefix}-deployable", type="deployable")
    model = client.models.create(
        name=f"{prefix}-base-model",
        category_id=id_int(category),
        manufacturer_id=id_int(manufacturer),
        model_number=f"BASE-{run_id}",
    )

    data = {
        "manufacturer": manufacturer,
        "category": category,
        "status": status,
        "model": model,
    }

    yield data

    with contextlib.suppress(Exception):
        client.models.delete(id_int(model))
    with contextlib.suppress(Exception):
        client.status_labels.delete(id_int(status))
    with contextlib.suppress(Exception):
        client.categories.delete(id_int(category))
    with contextlib.suppress(Exception):
        client.manufacturers.delete(id_int(manufacturer))


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def reset_state() -> Iterator[None]:
    saved = (state.url, state.api_key, state.config, state.json_output, state.verbose)
    state.url = None
    state.api_key = None
    state.config = None
    state.json_output = False
    state.verbose = 0
    yield
    state.url, state.api_key, state.config, state.json_output, state.verbose = saved


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        f"""\
[snipeit]
url = "{E2E_URL}"
timeout = 10
max_retries = 5

[custom_fields]
cpu_model     = "CPU"
cpu_passmark  = "CPU PassMark Score"
ram           = "RAM (GB)"
storage       = "Storage (GB)"
sale_price    = "Sale Price"
touch_screen  = "Touchscreen"

[pricing]
tiers = [[6, 100], [10, 125], [14, 150], [18, 175], [24, 200], [999, 250]]
touch_screen_bonus = 20
desktop_penalty    = 3

[passmark]
fuzzy_threshold = 80
"""
    )
    return config
