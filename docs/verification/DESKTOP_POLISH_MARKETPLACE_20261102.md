# Settings controls and marketplace acceptance

Date: 2026-11-02. Host: Windows. This pass continues the approved desktop design.

## Corrections

- Settings search inherited generic label styles: 45 px height, a 30 px icon/text
  gap and 14 px vertical padding. It now has a fixed 36 px height, 8 px gap,
  10 px horizontal padding and no flex shrink. Generic appearance-label rules
  no longer leak into embedded management forms or search fields.
- Current-tab borders used a global color transition. A measured intermediate
  frame showed both the previous and new tab underlined. The indicator now changes
  immediately. Hover and keyboard focus remain separate from current selection.
- All desktop selects share a Radix-based control with consistent typography,
  checkmarks, disabled states, typeahead, keyboard movement and explicit selection.
  Popups inherit the selected theme and stay inside the current dialog layer.
  Escape dismisses a popup before its parent settings dialog and cannot interrupt
  an agent through a menu. Popup text stays opaque throughout its short motion.
- Settings search supports Arrow Down into results, navigation supports arrows
  and Home/End, and closing Settings clears its search filter. Form and dropdown
  spacing remains consistent in compact layouts.

## Skill discovery

- Marketplace loads featured skills and two independent source lists on arrival.
  Featured skills are explicit Machina selections enriched from returned catalog
  data. They contain no fabricated popularity counts.
- skills.sh contributes its public all-time installation ranking. SkillsMP
  contributes development keyword results sorted by GitHub repository stars.
  Stars and installs are labeled separately and are never added into a score.
- Search submits to both catalogs or the selected source. Duplicate GitHub skills
  merge their source metrics. A failed source shows its own availability notice
  while the available source stays usable. Queries and responses are bounded;
  successful discovery caches for 15 minutes and searches for two minutes.
  Concurrent matching requests share the same fetches.
- The skills.sh public page exposes structured ranking data. The adapter parses
  JSON, never executes page scripts, and reports a source failure if its format
  changes. SkillsMP's documented anonymous search quota still applies.
- Review opens in the same workspace with the catalog retained underneath.
  Installation still requires the exact reviewed repository revision, files,
  digest and explicit confirmation. This pass installed no third-party skills.

Primary source contracts: [skills.sh](https://www.skills.sh/),
[SkillsMP API](https://skillsmp.com/docs/api),
[Radix Select](https://www.radix-ui.com/primitives/docs/components/select).

## Evidence

| Check | Result |
| --- | --- |
| Desktop TypeScript and unit/integration tests | 189 passed across 38 files |
| Shared contracts / artifact launcher tests | 4 / 3 passed |
| Python tests with disposable PostgreSQL | 153 passed |
| Edge browser workflows | 36 passed; optional private-history replay skipped |
| Ruff / formatting | Passed; 327 Python source/tool/test files formatted |
| Production dependency audit | No vulnerabilities reported |
| Native Windows engineering build | Passed, published beside the locked older executable |

The full browser suite includes compact dark settings, keyboard confirmation,
Escape/focus behavior, current-tab styling, multi-source discovery and exact review
handoff, plus existing editor, recovery, history, terminal and long-chat workflows.
The final long-chat run retains 10 rendered rows and measured approximately 7 ms
frame p95 locally; this is not a native or cross-machine FPS guarantee.

A live public-catalog smoke check returned both sources ready, three featured
skills and populated rankings. Cold discovery took 2.98 seconds; the cached repeat
took 0.0029 seconds on this host. No model prompts or user skill installations
were involved.

The rebuilt native Windows app was also opened and exercised by keyboard. Its
marketplace rendered real featured entries and populated both source sections,
with loading finished. The window remains open on that view. Native captures
remain in ignored acceptance storage because the sidebar contains local session
titles. No conversation or model prompt was submitted.

The committed screenshots are synthetic browser fixtures:

- [Marketplace discovery](20261102-marketplace-discovery.png)
- [Compact dark dropdown](20261102-settings-dropdown-dark.png)

Actual native macOS interaction remains unverified here. Shared control behavior
is tested in Edge and does not depend on Windows-only shortcuts or native menus.
