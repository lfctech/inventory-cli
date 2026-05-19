# Changelog

All notable changes to **inventory-cli** are documented here. The format is
inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project follows [Semantic Versioning](https://semver.org/) (pre-1.0: minor
bumps for breaking changes or significant new features, patch bumps for fixes).

## 0.2.0 — 2026-05-19

### Tests and CI

- **Test suite added** (`pytest` + `pytest-httpx`). 138 tests covering the
  high-risk surface area:
  - **Tier 1 (pure logic)**: every threshold and tier in
    `core/pricing.py`; CPU-name cleaning, model-id extraction, and bundled
    CSV lookup in `core/passmark.py`; the three-tier
    `resolve_config_path` fallback and `load_config` defaults/overrides
    in `config.py`.
  - **Tier 2 (CLI integration)**: name-resolver behaviour against mocked
    Snipe-IT list responses (exact / ambiguous / missing); the
    `assets {get, create, update, price}` command paths end-to-end via
    `typer.testing.CliRunner` with HTTP layer mocked by `pytest-httpx`,
    including the create partial-success recovery message and the v0.4.0
    "noop-cancel" custom-field staging contract.
- **`pytest.ini`** with `filterwarnings = error` so unintentional
  warnings fail the build, mirroring the upstream library's stance.
- **`.github/workflows/ci.yml`**: GitHub Actions on push + PR running
  `ruff check` → `pyright` → `pytest -q` on Python 3.13. Total runtime
  ≈ 1 minute.

### New features

- **`--version` flag and `inventory version` subcommand**: print the package
  version and exit. Version is now resolved at runtime via
  `importlib.metadata` rather than a hardcoded string in `inventory/__init__.py`.
- **`--verbose` / `-v` flag**: forwards to the `snipeit` and `snipeit.http`
  loggers shipped with `snipeit-python-api`. `-v` enables retry / timeout /
  warning traces; `-vv` adds per-request HTTP traces (method, path, status,
  elapsed ms — never tokens or headers).
- **Configurable HTTP timeout and retry count**: new optional `timeout` and
  `max_retries` keys under `[snipeit]` in `config.toml`, forwarded to the
  underlying `SnipeIT` client. Defaults match the library (10 s / 3 retries).
- **Annotated lookup type aliases** (`inventory.commands._lookup`): the
  per-command `--id / --tag / --serial` and `--id / --name` typer options
  are now declared once and reused via `Annotated[...]` aliases.

### Bug fixes / behaviour

- **`assets create`**: post-create steps (refresh + custom-field staging +
  save) now run in their own try block. If the asset is created but custom
  fields can't be applied, the CLI prints a clear partial-success message
  pointing the user at `inventory assets update --id <id>` to retry, instead
  of crashing with a stack trace.
- **`asset.save()` now also catches `RuntimeError`**: `Asset.save()` raises
  `RuntimeError` on malformed/missing `custom_fields` shape (a v0.4.0
  edge-case). All save sites (`update`, `price`, `create` recovery) now
  surface this through the friendly error path instead of letting it bubble
  up as a stack trace.
- **`models list`** now uses `client.models.list_all(search=..., limit=N)`
  so requests larger than the server-side page size (typically 50) paginate
  transparently rather than silently truncating.

### Code quality

- `import os` moved to module top of `inventory/commands/assets.py`
  (was buried inside `upload_file`).
- The fallback `except Exception` block in `assets files download` is
  narrowed to `SnipeITException` so unrelated programmer errors aren't
  swallowed by the best-effort filename probe.
- `_resolve_asset` is now typed `-> Asset` (was `-> Any`); same for
  `_resolve_target_model -> Model`. The asset / model table and dict
  helpers are typed accordingly. Pyright now flags typos like
  `asset.serail = "X"` against declared fields.
- `assets price` writes back via the shared `_set_custom_fields` helper
  instead of duplicating the per-field stringification logic.
- `assets files list` and `assets files download` trust the documented
  `{"total": N, "rows": [...]}` shape returned by `client.assets.list_files`,
  via a small `_extract_file_rows` helper. Three speculative fallback keys
  (`files`, `data`, plus single-dict-as-item) removed.
- `inventory.commands._common.get_client()` now `atexit.register`s
  `client.close()` so the underlying `httpx.Client` is torn down
  deterministically and no `Unclosed client` warnings are emitted on
  abnormal exit.

### Documentation

- README: corrected misleading line that called config keys "DB column
  names" — they are **display labels** (the same string shown in the
  Snipe-IT UI). The CLI translates labels to underlying column names
  via the API.
- `CHANGELOG.md` (this file) and `RELEASING.md` added.

### Dependencies

- `pyproject.toml` now pins `snipeit-api` to the v0.4.0 release commit
  (`6b2e039a…`) instead of tracking the `dev` branch. This stabilises
  `uvx --from git+...` callers (such as ProcMan) so they don't pick up
  unintended breaking changes between releases. Once the `v0.4.0` tag is
  pushed to GitHub the pin can be switched to `tag = "v0.4.0"` for nicer
  ergonomics — see `RELEASING.md`. Bump intentionally by editing the
  `rev = ` (or `tag = `) line and refreshing `uv.lock`.

## 0.1.0

Initial release.

- `inventory init` — scaffold a starter `config.toml` under
  `~/.config/inventory/config.toml`.
- `inventory assets {get,create,update,price,label}` — core asset
  operations, with `--id / --tag / --serial` lookup, `--json` output,
  PassMark CSV fallback, and Snipe-IT label PDF generation.
- `inventory assets files {list,upload,download,delete}` — manage files
  attached to an asset.
- `inventory models {get,list,create,update,delete}` — manage Snipe-IT
  models.
- Three-tier configuration resolution: `--config`, `INVENTORY_CONFIG`,
  XDG (`~/.config/inventory/config.toml`).
- Two-tier API key resolution: `--api-key` / `SNIPEIT_API_KEY`.
- Bundled PassMark CPU CSV with rapidfuzz lookup and operator-prompt
  fallback for low-confidence matches.
