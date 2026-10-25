# Machina 1.2.10

Windows input/media fixes and a menu-driven `/auth`.

## `/auth`

`/auth` now opens a provider picker for ChatGPT, Grok, and GitHub Copilot.
Select a provider, then choose sign in, sign out, import, or status. This is
the same page for all three subscription providers; the top-level sign-in
aliases remain for compatibility.

## Windows media

The music bar now uses the Windows System Media Transport Controls API through
the WinRT projections. It reads the current session's title, artist, album,
playback state, position, and duration, and sends play/pause, next, and
previous commands to that session. Spotify, browsers, and other apps that
publish a media session are supported.

## Windows keys and diagnostics

Legacy Windows modifier-arrow encodings are normalized before composer/editor
dispatch. Alt+Arrow remains a portable word-motion fallback. Failed agent
turns now show the redacted typed provider error instead of only
`agent turn failed`, so a ChatGPT subscription/model error is visible.

## Verification

- Backend, UI, and Docker CI jobs pass.
- Windows media tests cover the bridge availability and metadata mapping.
- Wheel and source distribution checks pass.
