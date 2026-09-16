---
name: gesaku-docs
description: Read or update the gesaku repo's documentation. Use this skill when asked about project docs, to explain a system, or to update docs after code changes.
---

# Gesaku Documentation Skill

Two things this skill must never do: restate the doc index, or treat `fuel/` as
documentation. Both are owned elsewhere, so a copy here would only rot.

## Hazard: `fuel/` is runtime data, not docs

`fuel/` holds prompt material fed to the novel-writing model. Never route it
into coding context as if it were project docs, and never "clean it up" —
editing it changes what the model produces. The current files and their
consumers are owned by `Docs/overview.md` § Pipeline Fuel; `ls fuel/` shows
them. Do not list them here.

## Routing: one index, not a table

`Docs/overview.md` is the only doc index — module map, data flow, short
key-rule list, and links to every other doc. Read it first; when you add a doc,
add it to that index. Superseded docs live under `Docs/reference/archive/`.

## When to Update What

| Change | Update |
|---|---|
| New/changed module responsibility | `Docs/overview.md` module map, plus its own doc under `Docs/core/`, `Docs/pipeline/`, or `Docs/systems/` |
| Path helper signatures, file layout | `Docs/core/path-resolution.md` |
| Phase behavior, retry logic, thresholds | `Docs/pipeline/spec.md` (state/git plumbing → `state-and-git.md`) |
| Scoring, penalties | `Docs/pipeline/scoring-engine.md` |
| CLI flags, env vars | `README.md` + `AGENTS.md` |
| Prompt template added/changed | the `prompts/*.md` file itself; conventions noted in `Docs/core/prompt-management.md` |
| New judge JSON contract | a Pydantic model in the validation module, documented in `Docs/core/output-validation.md` |
| Webui / console bridge | `Docs/systems/console-bridge.md` |
| Offline suite added/changed | `Docs/reference/test-suites.md` |

## Guidelines

- Docs describe the code as it is — if docs and code disagree, fix one of them
  now; never leave a note saying they disagree.
- `AGENTS.md` is for volatile run state (launch commands, monitor quirks, env
  overrides) and is gitignored, so edits there are local-only. Durable rules
  belong in the skills. Do not duplicate between the two.
- Do not move the `fuel/` files; the generators read them from root paths.
- Prefer invariants over inventories: "X is the single owner of Y" outlives
  "there are N modules".
