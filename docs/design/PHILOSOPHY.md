# Signal Grid

Machina's interface is a **typed instrument**, not a dashboard. The transcript
is the product. Chrome exists only when it carries live state.

This replaces the earlier "quiet cards" direction. Cards, pills, nested panels
and decorative borders are not part of the language.

## 1. Content is the interface

- The conversation has no outer frame.
- Assistant prose is the primary visual object.
- Tool calls are events, not cards.
- The shell is one persistent command strip: brand, the three screens, session
  context and live state. It stays because every part carries state.
- Bounded cards are reserved for grouped evidence: approvals, Activity
  categories, and prompt change blocks. The conversation itself stays
  unframed; cards never wrap prose.

## 2. One grid

- Two-cell transcript gutter, four-cell tool gutter.
- Assistant prose measure: 96 columns.
- Tool activity measure: 108 columns.
- Prompt bands and composer rules span the full usable width.
- On narrow terminals the gutters collapse before any content is hidden.

## 3. One signal

- `moonstone` is Nocturne: obsidian, bone, one acid-lime signal.
- `codex` is Ink & Sage: green-black, bone, one soft-sage signal.
- The accent marks **current**, **live**, or **actionable**. It is never used
  to decorate a heading or fill a card.
- Success, warning and danger are separate semantic inks, never the accent.
- Every state keeps a text glyph so meaning survives monochrome terminals.

## 4. Type does the work

- Hierarchy comes from scale, weight, case and space, not boxes.
- Micro-labels are uppercase and dim: `FILES`, `REVIEW`, `STATUS`.
- Body text is bone. Metadata is muted. Empty structure is faint.
- Large weights are reserved for the wordmark, selected rows and approval
  titles.

## 5. Turn rhythm

Every turn has the same shape:

```text
01 ❯ the user's prompt on a full-width bone band

  ⏺ assistant prose, unframed

    ✔ read_file install.sh                              done 0ms
    ⎿ the result, at most two preview rows

  ✻ done in 8s · 2 tools · 1.4k tokens
```

- Prompts are numbered. The number is part of the band, not a separate column.
- Assistant replies start with `⏺`.
- Tool results start with `⎿`.
- The turn closes with a `✻` colophon, not another rule.
- A cancelled turn closes with `⊘ stopped after ...`.

## 6. Negative space is structure

- One blank row between semantic blocks.
- Two blank rows around approval evidence.
- No blank row is decorative. It separates different kinds of information.
- Do not fill empty space with metadata. Let the transcript breathe.

## 7. Motion is a pulse

- One glyph, one cadence: `✻ ✶ ✳ ✢`.
- Working text says what is happening and how to stop it.
- Reduced motion keeps the glyph static.
- No layout animation, no bouncing, no sweeping bands.

## 8. Honest states

- Missing completion is `unknown`, never success.
- Denied is not an error and not a success.
- Truncation is named.
- Approval is bound to the exact action.
- A loop stop says why it stopped and what evidence remains.

## Banned

- Cards around assistant replies.
- Filled filter pills.
- Nested boxes.
- Bracket-wrapped tabs.
- Side-stripe accents on content.
- Color as the only state signal.
- Decorative gradient-like ASCII art.
- A second accent used for variety.

## Code map

- Palette and surfaces: `ui/src/design/theme.ts`, `ui/src/design/tokens.ts`
- Prompt band and composer: `ui/src/components/UserTurn.ts`,
  `ui/src/components.ts`
- Prose and turn colophon: `ui/src/components/AssistantTurn.ts`,
  `ui/src/components/TurnSummary.ts`
- Tool events: `ui/src/components/ToolTurn.ts`
- Command strip: `ui/src/components/NavBar.ts`
- Cards: `ui/src/design/box.ts`
- Screens: `ui/src/components/StatusPage.ts`,
  `ui/src/components/ChangesPage.ts`
- Workspace chrome: `ui/src/components/EditorPane.ts`
