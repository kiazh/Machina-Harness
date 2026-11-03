# Machina desktop: personal agent workspace

Latest direction, 2026-10-30: the user rejected the ornate design explorations
as clunky, approved the cleaner revision, and requested stronger blue-and-white
character. Use restrained medieval serif identity, a small animated mark,
ink-blue sidebar, ivory reading text, fewer borders and duplicate status controls,
and a spacious agent-first conversation. Keep code available on demand. Preserve
custom wallpapers, accents, motion and typography controls; do not restore the
oversized marketing hero or decorative full-canvas artwork as the default.

2026-10-29: user approved all three desktop directions, Volt, Afterimage and
Chrome, with Afterimage as the proposed default. They explicitly superseded the
initial restrained editor-first design: this is agent-centric and expressive.
Sessions/navigation sit on the left, agent conversation and execution occupy
the main area, and editable code opens alongside on demand.

Their supplied Nous/Hermes references emphasize ink blue, engraved illustration,
halftone grain, oversized editorial serif and condensed technical typography.
Carry those visual principles into an original Machina identity, without copying
the reference logos or seeding their illustrative content as application state.

## Fidelity inventory

- Afterimage: midnight ink, engraved mechanical artwork, serif display type,
  warm electric accent and translucent materials.
- Volt: coal and acid green, condensed display/navigation type, square controls,
  wider composer and a sharper technical rhythm.
- Chrome: silver/light materials, centered geometric identity, precise sans
  typography and bright blue accent.
- Narrow left session/navigation sidebar, broad central agent conversation,
  and an optional right-hand editor. Files remain editable, not only previews.
- Self-hosted variable fonts, independent UI/code font and size controls.
- Local custom wallpapers; adjustable surface opacity, glass blur and accent.
- A state-driven ASCII Machina glyph morphs between M, torus and cube. Motion
  can be expressive, subtle or off; system reduced motion takes priority.
- Compact document tabs and a quiet workspace/header row.
- Agent progress and concise tool evidence in the main area, without chat bubbles
  around every line or nested cards. Expand details deliberately.
- Command controls remain visibly attached to command activity: Background,
  Output and Stop. Show only actual command state and verified results.
- Queued prompts immediately above the composer; Enter queues during work.
- Temporary live subagent inspection remains subordinate to the main workspace.
- Real diffs with accept/reject review, rather than decorative color swatches.

The image is a direction artifact. Its example task text, URLs, timestamps and
test outcomes are illustrative, never seed content or product state.

## Interaction and responsive rules

The editor starts hidden unless recovering unsaved work. Opening a file reveals
it alongside the agent; hiding it retains its actual instance and undo state.
Retain editor instances, drafts, undo history and transcript anchors during
resizing/settings/session inspection. At small desktop widths collapse the
explorer first, then allow editor/harness focus modes rather than squeezing
both below usable widths. Keep commands discoverable and keyboard reachable.
Support reduced motion, clear focus, system font fallback and independently
adjustable UI/code fonts. No daily F-key defaults.

## Implementation

Tauri 2 host + React/TypeScript renderer + CodeMirror 6. The Python gateway owns
the harness, authorization, workspace operations and model calls. The renderer
does not receive engine tokens or unrestricted shell APIs.

Inspect actual working screens at 1440×900, 1280×800 and 1024×768 and native
Windows/macOS webviews. Approval of this direction does not prove those checks
have passed; evidence belongs in the implementation progress ledger.
