# Machina 1.2.14

Paste sanitization for every clipboard path.

## What changed

Microsoft Terminal can deliver a right-click paste as a plain multi-character
input chunk rather than a bracketed-paste frame. That path now receives the
same ANSI/SGR/mouse-report sanitizer as bracketed paste and Ctrl+V. Extended
colour forms such as `[38:2::r:g:b m` are also removed, including when the
escape prefix was lost during a terminal copy.

Ordinary prose, paths, line breaks and image handling are unchanged.

## Verification

- UI typecheck, lint, and 1,162 tests pass.
- Backend and Docker CI jobs pass.
- Wheel and source distribution checks pass.
