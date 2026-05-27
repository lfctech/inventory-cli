# inventory CLI

A Python CLI for managing [Snipe-IT](https://snipeitapp.com) assets with a clean, composable command-line tool.

Built with [`uv`](https://docs.astral.sh/uv/) and [`typer`](https://typer.tiangolo.com/).

---

## Quick Start

### With `uvx` (no clone required)

```bash
# Set API key (URL is in config.toml [snipeit].url)
export SNIPEIT_API_KEY="your-api-key"
export INVENTORY_CONFIG="/path/to/your/config.toml"

# Run any command
uvx --from git+https://github.com/lfctech/inventory-cli inventory assets get --tag LFC-1042
```

### Local development

```bash
git clone https://github.com/lfctech/inventory-cli
cd inventory-cli
uv sync
uv run inventory --help
```

---

## Configuration

Generate a starter `config.toml`:

```bash
uv run inventory init
# or with uvx:
uvx --from git+https://github.com/lfctech/inventory-cli inventory init
```

This writes a template to `~/.config/inventory/config.toml`. Edit the `[custom_fields]` section so each value matches the **display label** shown in your Snipe-IT UI (for example `"RAM (GB)"`). The CLI translates labels to the underlying column names automatically via the API.

Config resolution order:
1. `--config <path>` CLI flag
2. `INVENTORY_CONFIG` environment variable
3. `~/.config/inventory/config.toml`

---

## Authentication

No `.env` file loading. Credentials are resolved as follows:

**URL** — three-tier resolution (first match wins):

| Priority | Source |
|----------|--------|
| 1 | `--url` CLI flag |
| 2 | `SNIPEIT_URL` environment variable |
| 3 | `config.toml` → `[snipeit].url` |

**API key** — two-tier resolution:

| Priority | Source |
|----------|--------|
| 1 | `--api-key` CLI flag |
| 2 | `SNIPEIT_API_KEY` environment variable |

Or pass both directly:

```bash
inventory --url https://... --api-key token123 assets get --tag LFC-1042
```

---

## Commands

```
inventory
├── init                     Write a starter config.toml
├── assets                   Manage Snipe-IT assets
│   ├── get                  Fetch and display an asset
│   ├── create               Create a new asset
│   ├── update               Update fields on an existing asset
│   ├── price                Calculate and set the sale price
│   ├── label                Generate and save the label PDF for an asset
│   └── files                Manage files attached to an asset
│       ├── list             List all files attached to an asset
│       ├── upload           Upload one or more files to an asset
│       ├── download         Download a file from an asset
│       └── delete           Delete a file from an asset
└── models                   Manage Snipe-IT models
    ├── get                  Fetch and display a single model
    ├── list                 List models
    ├── create               Create a new model in Snipe-IT
    ├── update               Update one or more fields on an existing model
    └── delete               Delete a model
```

### Lookup flags (available on all `assets` commands)

Assets can be looked up by ID, asset tag, or serial number:

```bash
inventory assets get --id 142
inventory assets get --tag LFC-1042
inventory assets get --serial SN12345
```

### Creating assets with new models

`assets create` normally fails if `--model` does not exactly match an existing
Snipe-IT model. Pass `--create-model` to create the missing model first:

```bash
inventory assets create \
  --model "Latitude 5450" \
  --status Intake \
  --create-model \
  --category Laptop \
  --manufacturer Dell
```

When a model must be created, `--category` and `--manufacturer` are required.
Optional model fields are available with `--fieldset`, `--model-number`, and
`--notes`.

### JSON output

All commands support `--json` for machine-readable output:

```bash
inventory --json assets get --tag LFC-1042 | jq .
```

---

## Pricing Algorithm

Points are calculated from hardware specs, then mapped to a price tier:

| Component | Rule |
|-----------|------|
| CPU | `round(PassMark / 1000)` points |
| RAM ≥ 16 GB | +3 points |
| RAM ≥ 32 GB | +10 points |
| Storage ≥ 256 GB | +2 points |
| Storage ≥ 512 GB | +4 points |
| Desktop category | −3 points |

| Max Points | Price |
|------------|-------|
| 6  | $100 |
| 10 | $125 |
| 14 | $150 |
| 18 | $175 |
| 24 | $200 |
| 999 | $250 |

**Touch screen bonus:** +$20

Tiers and bonuses are configurable in `config.toml`.

### PassMark Resolution

1. `--passmark` CLI flag → used directly
2. Existing `cpu_passmark` value in Snipe-IT → used silently
3. Fuzzy match against bundled `PassmarkCPUList.csv` → auto-accept if ≥ 80% confidence
4. Low confidence → prompt operator to confirm

---

## ProcMan Integration

```powershell
$env:SNIPEIT_API_KEY = $secrets.SnipeItKey
$env:INVENTORY_CONFIG = "$PSScriptRoot\inventory\config.toml"
# URL is read from config.toml [snipeit].url

uvx --from git+https://github.com/lfctech/inventory-cli@vx.x.x inventory assets price --serial $serial
```
