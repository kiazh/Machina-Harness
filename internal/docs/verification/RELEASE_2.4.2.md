# Machina 2.4.2

Delegation survives a broken provider key.

## Fixed

- **A free Hermes parent's route is sticky for delegated children across all
  providers.** The connected-model router ranks routes from credential
  *presence*, not credential health. In the reported failure it ranked
  `deepseek/deepseek-v4-pro` (HTTP 402: account has no balance) and
  `openrouter/minimax-m3.1-flash:free` (HTTP 401: invalid key) above the
  parent's working `hermes/meituan/longcat-2.5:free`, so every subagent died
  before doing any work. `keep_free_hermes_parent_route` now returns the free
  Hermes parent route whenever the routed route differs, instead of only when
  the router stayed on Hermes. An explicit agent-definition pin still wins.
- **Provider failures name the cause.** Failed turns and delegated children now
  report the HTTP status, provider and model, plus the actionable fix:
  - 401/403: "Check the provider credential."
  - 402: "Add credits or switch provider/model."
  The generic "LLM provider error. Check server logs." remains only when no
  detail is available.

## Verification

- Repository suite: `729 passed, 18 skipped`; ruff check and format clean.
- Unit tests: the sticky guard rejects both observed routes; the failure detail
  formats 401 and 402 correctly.
- Real delegation run with a forced bad override
  (`provider_override=deepseek`, `model_override=deepseek-v4-pro`) under a free
  Hermes parent: the child ran on `hermes/meituan/longcat-2.5:free`, returned
  `OK`, and finished `complete`.

### Approvals

- `approval_timeout` defaults to `0` and now means "wait for the human".
  Pending approvals no longer expire after five minutes: walk away for an hour
  and the card is still waiting on return. A positive value restores a bounded
  wait; the config accepts `0` and no upper bound. Cancelling the turn is still
  the explicit way to retire a card without a decision.

## Operational note

The machine that produced the failure has two unusable credentials:

- `OPENROUTER_API_KEY` returns 401 (rotate or remove it).
- `DEEPSEEK_API_KEY` returns 402 (add credits or remove it).

Machina now keeps delegations on the working free Hermes route regardless, but
those keys will still fail if selected explicitly or by another route.

## Compatibility

- No database migration. Existing sessions keep their pinned routes; retry the
  failed delegation after restarting Machina.
