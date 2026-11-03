# Cleaner blue-and-white desktop revision

User approved the cleaner layout and asked for more blue/white character.
Implemented smaller serif identity, ink-blue sidebar, ivory text, quieter tool
rows, fewer duplicate controls, composer-side connection recovery and an explicit
agent Stop. Rich wallpaper/material/font/motion settings remain available.

Assistant Markdown is rendered safely with inert HTML/images and separate-context
links. Mounted streaming replies are formatted at most every 125ms and completed
replies display their final text immediately. Existing virtualization, lazy editor
loading, buffers and recovery are preserved.

Verification on Windows x64:

- `npm run desktop:check`: TypeScript and 32 tests passed.
- Playwright/Edge: 6 scenarios passed, including new visible-cancel flow.
- Long synthetic chat: 10 rendered rows; sampled rAF p95 13.8ms during native build.
- `npm run desktop:native`: final optimized build passed after the Markdown safety
  changes. Native release connected to the engine and opened README.md.
- Focused independent source/screenshot review: no Important/Critical findings.

Actual renderer screenshots: `design-previews/09-blue-white-working.png` and
`10-blue-white-empty.png`. Working-session content is synthetic test-fixture data.
This evidence does not close the broader desktop parity or native macOS gates.
