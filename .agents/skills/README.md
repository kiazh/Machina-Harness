# Matt Pocock skills for Machina

Source: <https://github.com/mattpocock/skills>  
Pinned commit: `4ae2317ddb22b046e26455e4f9c9b3aaaf62212d`  
Installed: 2026-11-10  
License: MIT; the upstream notice is in `MATTPOCOCK-LICENSE`.

This directory contains the upstream **promoted** skill set: every skill in
`skills/engineering/` and `skills/productivity/` (27 total). They are copied
verbatim so this project can use the author's real-engineering workflows with
OpenCode/Claude-style agent discovery.

## Applicable set

Engineering:

`ask-matt`, `code-review`, `codebase-design`, `diagnosing-bugs`,
`domain-modeling`, `grill-with-docs`, `implement`, `implement-spec`,
`improve-codebase-architecture`, `pr`, `prototype`, `research`, `retro`,
`setup-matt-pocock-skills`, `tdd`, `to-spec`, `to-tickets`, `triage`,
`wayfinder`, `wizard`.

Productivity:

`grill-me`, `grilling`, `handoff`, `teach`, `to-questionnaire`, `wait-what`,
`writing-for-agents`.

These fit Machina because the repo is a Python/TypeScript agent harness with
tests, architecture docs, ADR-style decisions, a CLI/TUI, PostgreSQL, and a
GitHub-backed workflow. The model-invoked skills are also useful for the
repo-repair and benchmark work: `diagnosing-bugs`, `tdd`, `research`,
`code-review`, `codebase-design`, `domain-modeling`, `prototype`, `pr`,
`wizard`, and `writing-for-agents`.

## Excluded buckets

- `skills/misc/`: upstream marks this bucket frozen and unmaintained; its
  entries are mostly Claude-specific or unrelated to this project.
- `skills/in-progress/`: beta skills that upstream does not ship in its
  promoted plugin set.
- `skills/deprecated/`: no longer used upstream.

`setup-matt-pocock-skills` is installed but not configured. Run it only when
you want to bind the workflows to this repo's issue tracker, labels, and docs
layout. It may ask configuration questions and should be run deliberately.
