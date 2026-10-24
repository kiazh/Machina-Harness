# Machina 1.2.6

Windows virtualenv repair.

## What changed

The Windows installer now validates the virtualenv before trusting it:

- If `.venv` was created by a different Python version than the one selected
  for the installation, the installer rebuilds it. Extension packages such as
  `asyncpg` are ABI-specific and cannot be safely carried across versions.
- After installing dependencies it imports `typer`, `asyncpg`, and `fastapi`.
  If that fails, it rebuilds the venv once and retries.
- If it still fails, the installer stops with an actionable message instead of
  reporting success and letting every `machina` command traceback.

This fixes the Windows failure:

```text
ImportError: cannot import name '_base' from 'asyncpg.exceptions'
```

which came from a partial or version-incompatible `asyncpg` installation in an
existing `.venv`.

## Manual repair

```powershell
Remove-Item -Recurse -Force C:\Users\kiash\machina\.venv
irm https://raw.githubusercontent.com/kiazh/Machina-Harness/main/install.ps1 | iex
```

The checkout and `.env` are preserved; only the virtualenv is rebuilt.

## Verification

- Backend, UI, and Docker CI jobs pass.
- Installer syntax and import-health checks pass.
- Wheel and source distribution checks pass.
