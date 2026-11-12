# Machina Desktop Design

Machina's desktop visual contract. It borrows the product philosophy of Nous
Research's Hermes Desktop and keeps Machina's own ember identity and OpenCode
typography.

## Principles

1. **Chat is the home surface.** The transcript and composer stay primary.
   Sidebar, files, changes, terminal, and previews are working context.
2. **Flat, not boxed.** Group with whitespace and a single hairline. Do not nest
   rounded panels or add shadows to in-page content.
3. **One surface per concern.** The composer is one surface. A user message is
   one surface. Tool activity is a flat list, not a card of cards.
4. **Progressive disclosure.** Tool names, paths, timings, and check results are
   visible at a glance. Detail belongs behind an explicit action, not in the
   default transcript.
5. **Quiet state.** Model, branch, context use, and approval mode live in a
   compact lower status bar. They do not compete with the conversation.
6. **Intent before automation.** A result never opens a pane or moves focus on
   its own. The operator chooses when to inspect.
7. **No decorative controls.** If it looks pressable, it must be pressable. Do
   not add "Collapse" or "Open" labels without behavior.

## Identity

- Machina ember is the brand and primary accent.
- OpenCode typography and neutral tokens come from `rune-theme-opencode`.
- Local/live state uses a green dot; warnings use amber; failures use red.
- Light and dark are equal appearances, not inversions of a default.

## Layout

- Left: session sidebar with a flat New chat row, recent sessions, and account.
- Center: session title, quiet metadata, transcript, tool activity, composer,
  then the status bar.
- Right: workspace inspector with underlined Changes/Files tabs, flat changed
  files, check status, and the workspace path.
- The inspector is resizable and can be collapsed from the toolbar.

## Rune implementation

- All app UI is in `src/main.rs`; use `rune-kit` primitives where they fit.
- Prefer `Kit` semantic tokens (`k.c.*`, `k.ty.*`, `k.radius.*`). No raw hex.
- Use `rune::text_input` inside the composer so the field is borderless and the
  composer owns the only surface.
- Keep `chat-scroll`, `composer-input`, and `send` keys stable; headless tests
  use them.
- Check the design with:

```bash
cargo test --locked
cargo run -- --snap docs/rune-shell-dark.png
cargo run -- --scene light --snap docs/rune-shell-light.png
```
