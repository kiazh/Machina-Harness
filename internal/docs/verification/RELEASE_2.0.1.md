# Machina 2.0.1

The verification and diagnostics patch for 2.0.0.

2.0.0 shipped a large consolidation: a typed gateway seam, a single turn
engine, snapshots, ACP, service lifecycle, update/rollback, and expanded
provider integrations. This patch closes the verification gaps around that
release. It does not add a new feature tranche.

## Fixes

- **Windows connector ids.** `derive_connector_name` now splits Windows
  executable paths with `ntpath` before deriving the id, so
  `C:\Program Files\node.exe` becomes `node` on every host. Previously the
  POSIX `Path` implementation produced `c-program-files-node` outside
  Windows.
- **Host setup planning.** `plan_setup` branches on the inspected
  `HostStatus.platform` instead of the interpreter's own `_is_windows()` /
  `_is_wsl()` checks. WSL already reports `platform="windows"`; Linux, macOS,
  and tests now plan the same steps the status object describes.
- **Typed config failures.** `validate_value` keeps its `ValueError`
  compatibility but raises `ValidationError`, so a malformed `MACHINA_*`
  override reaches the gateway as an actionable message instead of a bare
  `ValueError`.
- **Typed pool failures.** Invalid `MACHINA_DB_MIN_POOL`,
  `MACHINA_DB_MAX_POOL`, and `MACHINA_DB_TIMEOUT` values now name the variable
  and fail as `ValidationError`.

## Diagnostics

- Added `machina debug logs [--lines N] [--name FILE]`. Turn tracebacks are
  written to `gateway.log`; the UI intentionally shows only a short, redacted
  message. The new command is the supported cross-platform way to read that
  traceback.
- Untyped turn failures now append
  `run \`machina debug logs\` for the traceback` to the error toast.

## Verification

- The external regression suite now targets the 2.0 typed `Gateway.call`
  seam. The removed private `_RpcGateway` bridge is no longer imported or
  simulated; HTTP route tests still cover auth, redaction, typed error
  mapping, the shared app-state gateway, and concurrent dispatch.
- Repository smoke suite: `683 passed, 18 skipped` on the verification
  machine.
- UI suite: `1,260 passed, 6 skipped`, typecheck and lint clean.
- Ruff, format, and compile checks clean.
- External suite run against a real PostgreSQL 16 + pgvector instance with
  Python 3.11; CI is the authoritative matrix.

## Compatibility

- No database migration.
- No configuration change.
- No public API removal beyond the 2.0.0 breaking changes.
- `ValueError` handlers continue to catch config validation failures because
  `ValidationError` subclasses `ValueError`.
