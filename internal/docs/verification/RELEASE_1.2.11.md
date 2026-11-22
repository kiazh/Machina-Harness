# Machina 1.2.11

Clipboard paste sanitization.

## What changed

Pasting terminal output no longer inserts raw SGR/ANSI colour sequences into
the composer. The paste path strips:

- escape-prefixed ANSI control sequences;
- literal `[38;5;…m` / `[0m`-style residue left when a terminal copy crosses a
  renderer boundary; and
- non-printing control bytes.

Newlines, ordinary prose, paths, and image-drop handling are preserved. The
workspace code editor keeps its existing raw-text paste semantics.

This fixes the composer filling with long strings such as:

```text
[38;5;28m[38;5;80m…[0m
```

## Verification

- UI typecheck, lint, and 1,162 tests pass.
- Backend and Docker CI jobs pass.
- Wheel and source distribution checks pass.
