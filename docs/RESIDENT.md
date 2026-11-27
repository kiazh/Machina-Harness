# Maki, the resident companion

Status: **foundation implemented; connectors and screen control are next.**

Maki is a dedicated Machina session with a stable profile,
always-injected core memory, autonomy grants, and a native macOS notch client.
It is a normal turn engine with an identity and a policy layer — not a second
agent runtime.

## Pieces

| Piece | Location |
|---|---|
| Profile, core memory, grants, audit | `machina/resident/store.py` |
| Session bootstrap and status | `machina/resident/runtime.py` |
| Gateway methods | `machina/gateway/features/resident.py` |
| Resident-only tools | `machina/tools/resident.py` |
| Local notch token | `machina/resident/token.py` |
| Native notch app | `notch/` |
| TUI command | `ui/src/features/agent.ts` (`/agent`) |
| Schema | `machina/db/resident_schema.sql` |

## Authority model

- Default profile mode: `auto-safe`, mapped to the session's `workspace` mode.
  Reads, drafts, and workspace-local work can proceed; external sends and
  irreversible actions still need approval.
- `ask` maps to `ask`; `full` maps to `full`. The profile mode is applied to the
  resident session whenever it is opened.
- Grants are explicit rows with operation/session/target patterns, an optional
  action budget, and an expiry. They are created only by the user surface.
- The resident cannot create or extend grants. It can resolve an approval only
  when a grant matches the operation, session, and target.
- `resident_proxy_resolve` is resident-session-only; every resolution records
  `resident:<grant-id>` as the principal and writes an audit row.
- `resident.kill` revokes every active grant and pauses the profile.

## Gateway methods

`resident.status`, `resident.prompt`, `resident.context`, `resident.events`,
`resident.profile.set`, `resident.memory.get/set/forget`,
`resident.grant.create/list/revoke`, `resident.approval.list/resolve`,
`resident.kill`.

All are local-terminal capabilities. The notch reaches them over loopback
HTTP using the 0600 `~/.machina/notch.token`; the token is accepted only on
`/rpc` and enables the `resident.*` surface. `host.credentials` and
`interop.run` remain unavailable even with it.

## Memory

- `resident_core_memory` is always injected into the resident session and,
  when `shared_memory` is on, other chats. It is bounded to 24 rows / 1,600
  characters and rendered as stable facts.
- Recent frontmost-app context is injected only into the resident session,
  clearly marked untrusted.
- Episodic memory continues to use the existing memory tables and
  consolidation pipeline; the resident layer does not duplicate it.

## Notch

See [`notch/README.md`](../notch/README.md). The app is a Swift package with a
bundle script; it uses AppKit materials, a vector mascot, synthesized
non-verbal cues, and the loopback gateway.

## Next

1. ScreenCaptureKit + visible capture indicator; Accessibility control.
2. Composio connector adapter and native Mail/Calendar tools.
3. Batch engine UI and RSI preference proposals.
4. Developer ID signing/notarization and a launchd login item.
