# Releasing

This project uses [Semantic Versioning](https://semver.org/).

While pre-1.0:
- **MINOR** bumps for breaking changes or significant new features
- **PATCH** bumps for bug fixes and non-breaking additions

The CLI exposes its version at runtime via `importlib.metadata`:

```bash
inventory --version
inventory version
```

Both read from the installed package metadata, so they stay in sync with
`pyproject.toml` automatically — no `__version__ = "..."` literal to update.

## Release checklist

1. Update `version` in `pyproject.toml`.
2. Move `## Unreleased` entries in `CHANGELOG.md` under a new header:
   ```
   ## X.Y.Z (YYYY-MM-DD)
   ```
   Add a fresh empty `## Unreleased` section above it.
3. (If the underlying API changed) update the `tag = "vX.Y.Z"` pin for
   `snipeit-api` in `pyproject.toml` and run `uv lock` to refresh
   `uv.lock`. Verify the CLI still imports and runs (`uv run inventory --help`).
4. Commit: `git commit -am "release: vX.Y.Z"`
5. Tag: `git tag vX.Y.Z`
6. Push: `git push origin main --tags`

## Verifying after release

`uvx` callers (e.g. ProcMan) should resolve cleanly against the new tag:

```bash
uvx --from git+https://github.com/lfctech/inventory-cli@vX.Y.Z inventory --version
```

## Commit message convention

Use [Conventional Commits](https://www.conventionalcommits.org/) prefixes:

- `feat:` — new feature (bumps MINOR)
- `fix:` — bug fix (bumps PATCH)
- `docs:` — documentation only
- `test:` — test additions/changes
- `ci:` — CI/workflow changes
- `chore:` — maintenance (deps, config, cleanup)
- `refactor:` — code change that neither fixes a bug nor adds a feature
- `release:` — version bump commit
