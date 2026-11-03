# Desktop integration and final polish

Continue the approved agent-first workspace and its nine palettes. User requested
direct implementation. Keep the CLI independent and preserve existing settings,
buffers, drafts, consent and custom wallpapers.

1. Remove the marketplace's accidental conversation prerequisite. Human reviews
   without a conversation are scoped to the authenticated gateway; existing
   conversation-owned reviews retain session/agent ownership and exact digests.
2. Add browsable connector starters with honest setup and actual connection state:
   Google services, GitHub, self-hosted Firecrawl and an outgoing Machina bridge.
3. Observe real MCP and external CLI exchanges in a bounded, redacted local store.
   Add a dedicated, visibility-aware Communications tab with filtering and details.
4. Add inline syntax/LSP diagnostics, debounce requests, reject stale versions and
   retain ghost completion with Tab acceptance and normal indentation fallback.
5. Repair failed-load empty states, model no-match feedback, minimum-size navigation,
   and verify menus/forms against the current palettes and reduced motion.
6. Replace the built-in artwork with a generated blue/silver celestial engraving.
   Keep explicit custom/none choices and do not animate large background surfaces.

Acceptance: focused ownership, concurrency, failure and stale-response tests;
desktop typecheck/unit/integration/browser suite; isolated database backend suite;
native build and real Windows smoke test. Report macOS and real third-party OAuth
limits accurately. No real prompts, account connections or marketplace installs
are required for this acceptance pass.
