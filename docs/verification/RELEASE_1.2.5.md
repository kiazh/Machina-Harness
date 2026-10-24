# Machina 1.2.5

Windows Typer import fix.

## What changed

Typer 0.21.2 introduced an `annotated-doc` dependency. Fresh Windows installs
could resolve that package into an unusable namespace state, causing every
Machina command to fail before argument parsing:

```text
ImportError: cannot import name 'Doc' from 'annotated_doc' (unknown location)
```

Machina now constrains Typer to `<0.21.2`. Re-running the installer repairs an
existing virtual environment by downgrading Typer. No database or user data is
touched by this fix.

## Verification

- CLI imports and command help run with Typer 0.21.1.
- Backend, UI, and Docker CI jobs pass.
- Wheel and source distribution checks pass.
