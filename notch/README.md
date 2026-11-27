# Machina Notch — Maki (macOS)

The notch is a native SwiftUI/AppKit client for the resident Machina session.
It is a satellite, not a second brain: the Python gateway owns turns, memory,
tools, approvals, and authority.

## Run

```bash
machina serve --port 8000     # the gateway the notch talks to
./notch/bundle.sh             # builds build/MachinaNotch.app
open notch/build/MachinaNotch.app
```

`machina init` and the API lifespan create `~/.machina/notch.token` (0600).
The notch authenticates `/rpc` with that token; the token enables
`resident.*` only and never host credentials or harness execution.

The notch is an accessory app (`LSUIElement`): it has no Dock icon, lives in
the menu bar, and launches at login when enabled from its menu.

## Surface

- Collapsed: circular mascot over the notch, approval badge.
- Expanded: chat, waiting approvals, resident mode/memory count, mute/pause.
- Mascot states: idle, blink, thinking, processing, surprised, happy, waiting,
  sleeping. Vector-drawn with a silver radial face, inner rim, specular
  highlight, and a soft shadow.
- Sounds: synthesized chirp, happy trill, alert, boop, sleep/wake. No voice,
  no microphone, no wake word.
- Accessibility: reduced motion disables the blink/orbit animation.

## Current capabilities

- Live status and approval queue via `resident.*` over loopback.
- Chat: sends `resident.prompt` and polls `session.history` for the reply.
- Frontmost app/window/selection context is sent every 10s when
  Accessibility permission is granted (`resident.context`).
- Approval buttons resolve as the user. Grant-scoped proxy resolution is
  available to the resident through the `resident_proxy_resolve` tool.
- Manual gateway start: the app tries `machina serve` when the health check
  fails.

## Not yet wired

- ScreenCaptureKit screenshots and visible capture indicator.
- Accessibility-driven control (click/type) and Playwright browser tasks.
- Composio and Apple Mail/Calendar connectors.
- Batch dashboard and RSI review surfaces.
- Developer ID signing/notarization; the bundle script uses an ad-hoc
  signature for local use.

## Permissions

The app asks for nothing until a feature needs it. Accessibility is required
for window titles and selected text; Screen Recording will be required for
screenshots. Both are optional and can be revoked in System Settings.
