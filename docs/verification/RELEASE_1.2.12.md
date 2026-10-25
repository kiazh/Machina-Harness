# Machina 1.2.12

Mouse-report paste sanitization.

## What changed

Windows terminal copies can contain SGR mouse reports such as
`[<35;44;19M`, in addition to colour SGR runs. The composer sanitizer now
removes both the escape-prefixed form and literal residue before creating a
draft, and ignores a raw mouse report delivered as input. Ordinary prose,
paths, line breaks, and image handling are preserved.

## Verification

- UI typecheck, lint, and 1,162 tests pass.
- Backend and Docker CI jobs pass.
- Wheel and source distribution checks pass.
