# Desktop engineering preview: Windows evidence

2026-10-29, Windows x64. Working tree based on 86dd783; this is not a signed release.

## Scope checked

Agent-first conversation with an optional retained CodeMirror editor; Volt,
Afterimage and Chrome; self-hosted fonts; custom local wallpaper, material and
motion settings; bounded virtual transcript; steering and same-job controls;
local draft recovery; authenticated native engine and process ownership.

## Results

| Check | Result |
|---|---|
| `npm run desktop:check` | 27 passed, TypeScript passed |
| `npx playwright test --config apps/desktop/playwright.config.ts` | 5 passed in Edge |
| `node --test packages/contracts/test/contracts.test.ts` | 3 passed |
| Desktop bootstrap/runtime pytest files | 6 passed |
| Rust `cargo test --lib`, with actual interpreter and native smoke flags | 12 passed |
| `npm run check --prefix ui` | 1188 passed, 10 skipped, lint passed |
| `npm run desktop:native` | Optimized Windows client built |
| `npm run desktop:preview` | Automatic interpreter selection and native startup passed |

Real Rust subprocess checks initialized the engine, listed/read this workspace,
and obtained Pyright `Path` completion from an unsaved mirror. No user model prompt
was submitted. The smoke environment disabled scheduled jobs and database management.

The packaged native application connected, opened README.md, switched all three
styles, loaded a custom Blob wallpaper under the production CSP, and used the
bundled Manrope font. Closing the native window exited its Python child and the
preview launcher with status 0.

The synthetic long-chat browser scenario inserted 1500 messages, retained the
bounded 1000-row store, streamed while typing code, and mounted 10 message rows.
Sampled requestAnimationFrame p95 was 7 ms in the final run. This is a short
synthetic check, not an all-project or sustained native performance guarantee.

The personalization scenario verifies that pane/style changes preserve the actual
editor DOM instance and draft; custom wallpaper and motion settings survive reload;
and motion Off stops canvas changes. Two independent review findings were reproduced
RED and fixed GREEN: edits during document reopen, and recovery removal racing with
new edits after save.

Final client executable: **6,302,208 bytes (6.01 MiB)**. This includes the client,
artwork and fonts, and excludes Python, PostgreSQL and optional language tooling.
The development-engine build requires this configured checkout. It is not a
self-contained installer or a total installation-size measurement.

## Outstanding roadmap gates

Native macOS acceptance, full auth/model/command parity, runtime snapshot and
submission idempotency, complete ghost/navigation/diff/PTY features, managed engine
packaging, signing and safe updates. The existing terminal client remains available.
Current assistant canonical response accumulation is not capped with the visible
transcript; this lower-severity review finding is deferred.

## Artwork and fonts

`public/art/afterimage.png` is original generated artwork shaped by the user's
engraving/halftone references. No reference logos or sample tool outcomes are used
as product state. Bodoni Moda, Oswald and Manrope are self-hosted; their OFL license
texts are included under `public/licenses/`.
