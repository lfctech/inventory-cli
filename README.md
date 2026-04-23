# inventory CLI

A Python CLI for managing [Snipe-IT](https://snipeitapp.com) assets — replacing the `inventory-resources` scripts with a clean, composable command-line tool.

Built with [`uv`](https://docs.astral.sh/uv/) and [`typer`](https://typer.tiangolo.com/).

---

## Quick Start

### With `uvx` (no clone required)

```bash
# Set credentials
export SNIPERIT_URL="https://inventory.lfctech.org"
export SNIPERIT_API_KEY="your-api-key"
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

No `.env` file loading. Set credentials in the environment:

| Variable | Description |
|----------|-------------|
| `SNIPERIT_URL` | Snipe-IT instance URL |
| `SNIPERIT_API_KEY` | API bearer token |

Or pass them directly:

```bash
inventory --url https://... --api-key token123 assets get --tag LFC-1042
```

---

## Commands

```
inventory
├── init                     Write a starter config.toml
└── assets
    ├── get                  Fetch and display an asset
    ├── create               Create a new asset
    ├── update               Update fields on an existing asset
    ├── price                Calculate and set the sale price
    └── label                Download the label PDF
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

| Min Points | Price |
|------------|-------|
| 0 | $100 |
| 7 | $125 |
| 11 | $150 |
| 15 | $175 |
| 19 | $200 |
| 25 | $250 |

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
$env:SNIPERIT_URL     = "https://inventory.lfctech.org"
$env:SNIPERIT_API_KEY = $secrets.SnipeItKey
$env:INVENTORY_CONFIG = "$PSScriptRoot\inventory\config.toml"

uvx --from git+https://github.com/lfctech/inventory-cli inventory assets price --serial $serial
```
