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

This writes a template to `~/.config/inventory/config.toml`. Edit the `[custom_fields]` section to match your Snipe-IT instance's custom field DB column names.

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

### `inventory assets create`

```bash
inventory --config config.toml assets create \
  --model "Laptop" \
  --status "Intake" \
  --serial "SN12345" \
  --cpu "Intel Core i7-1165G7" \
  --ram 16 \
  --storage 512
```

### `inventory assets update`

```bash
inventory assets update --tag LFC-1042 --status "Refurbished" --sale-price 150
```

### `inventory assets price`

```bash
# Full auto — reads RAM/storage/touch from asset, looks up CPU in PassMark DB
inventory assets price --tag LFC-1042

# With overrides
inventory assets price --tag LFC-1042 --passmark 9420 --ram 16 --storage 512

# Dry run — print price without writing
inventory assets price --tag LFC-1042 --dry-run
```

### `inventory assets label`

```bash
inventory assets label --tag LFC-1042 --output ./LFC-1042.pdf
```

### `inventory assets files`

```bash
# List files attached to an asset
inventory assets files list --tag LFC-1042

# Upload files to an asset
inventory assets files upload report.pdf diagram.png --tag LFC-1042 --notes "Initial intake"

# Download a file from an asset
inventory assets files download --file-id 14 --tag LFC-1042 --output ./report-downloaded.pdf

# Delete a file
inventory assets files delete --file-id 14 --tag LFC-1042 --force
```

### `inventory models`

```bash
# List models
inventory models list --limit 10

# Get a specific model
inventory models get --id 4

# Create a model
inventory models create --name "Latitude 7420" --category "Laptops" --manufacturer "Dell" --fieldset "Laptop Specs"

# Update a model
inventory models update --id 4 --model-number "L7420"

# Delete a model
inventory models delete --id 4 --force
```

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
5. No match → use CSV average, print warning

---

## ProcMan Integration

```powershell
$env:SNIPEIT_API_KEY = $secrets.SnipeItKey
$env:INVENTORY_CONFIG = "$PSScriptRoot\inventory\config.toml"
# URL is read from config.toml [snipeit].url

uvx --from git+https://github.com/lfctech/inventory-cli inventory assets price --serial $serial
```
