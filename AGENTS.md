# Machina

## Design philosophy: one click, then it just works

This is non-negotiable and applies to every feature, installer, provider, and
integration.

- The user performs **one action**: Setup. Everything else is Machina's job.
- Setup provisions every dependency, database, PATH entry, certificate, and
  external tool it can, then launches and verifies the app. Never report
  success unless the post-setup health check actually passed.
- Never require a user to clone a repository, edit a file, create a venv,
  install requirements, set environment variables, or run a follow-up
  command. If a step can be automated, it must be automated.
- If a capability needs an external tool, detect it first. When it is
  missing, install it from its official source as part of the same Setup
  action, then configure, start, and sign in.
- The only acceptable human steps are unavoidable third-party consents
  (OAuth approval, captcha). Hand those off as a browser/terminal window
  opened by Setup, not as a terminal instruction the user must copy.
- Errors must never be the setup path. A missing tool or login produces a
  single actionable Setup action, not a manual checklist.

## Agent skills

### Issue tracker

GitHub issues in `kiazh/Machina-Harness`, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` and `docs/adr/`. See `docs/agents/domain.md`.
