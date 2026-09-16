---
name: gesaku-style
description: Applies the gesaku repo's Python coding style and structural rules. Use this whenever writing or reviewing code in the gesaku novel-pipeline repo.
---

# Gesaku Coding Style

Rules only. The module map, data flow, and full key-rule list are owned by
`Docs/overview.md` — read it before writing code, and trust it over this file
for anything that names a file. Everything below is the subset that stays true
when files move.

## Where code goes (by kind, not by path)

| Layer | Holds | New code rule |
|---|---|---|
| shared library | no pipeline-specific logic | a reusable helper goes here |
| foundation generators | one script per foundation document | new document → new script |
| pipeline stage modules | one module per stage | new stage → new module |
| orchestrator | phase sequencing + the CLI only | no library logic here |

- Dependency direction is one-way: shared library ← foundation/pipeline ←
  orchestrator. A generator must never import a stage module.
- **Verify, don't remember**: `ls core pipeline foundation` before importing.
  Module *names* are a contract; directory layout is a convention.

## Hard Rules

1. **Path access via the path module only** (`from core import paths`). No
   hardcoded `projects/<name>/...` strings. Any user-derived project name must
   pass `paths.set_project_name` (the path-isolation check).
2. **LLM JSON → Pydantic, not dict-hope.** Define a model in the validation
   module, parse with `parse_validated(Model, text)`, and on
   `OutputValidationError` feed `.feedback` into the self-correction retry.
   Never re-implement regex extraction for JSON a judge already returns.
3. **Static prompts → `prompts/*.md`**, loaded with `paths.load_prompt(name)`.
   Templates keep `{placeholder}` / `{{escaped}}` braces exactly as the old
   inline literals did, so `.format()` call sites are unchanged.
4. **Atomic writes only**: tmp file + `os.replace`, cleanup on failure
   (`paths.save_json_atomic`, `paths.save_registry`). Never write state,
   registry, sidecars, or eval logs in place.
5. **Tests run offline.** Install the mock LLM (`MockLLM.install()`) — it
   rebinds import-time references, so install before *or* after importing
   pipeline modules. Run with `uv run python -m unittest tests.test_<name>`.
   A suite that needs an API key belongs in E2E, not `tests/`.
6. **One owner per fact.** The genre config owns the chapter count; the infra
   module owns timeouts and tolerances. Mirror a value, never re-derive it.
7. **Named budgets, never literals.** Timeouts and tolerances come from
   `timeout_for(...)` / `*_threshold` / `*_tolerance` — no per-call-site magic
   numbers.

## Conventions

| Convention | Rule |
|---|---|
| Python | 3.12+, run everything through `uv run` |
| Encoding | explicit `encoding="utf-8"` on every read_text/write_text/open |
| Style | plain functions over classes; module-level globals with lazy init |
| Docstrings | public functions only, one line where possible |
| Errors | raise with actionable messages; no bare `except Exception: pass` in new code |
| Retries | LLM calls go through `call_llm`'s backoff; phase-level retries belong to the phase layer |
| Comments | non-obvious logic only; the code is the source of truth |

## What to Avoid

- ❌ New monoliths: past ~500 lines, split by concern — the module splits in the
  refactor history are this rule being applied, not a coincidence
- ❌ Regex-parsing judge output that is already JSON
- ❌ Silent score fallbacks (`return -1.0`) hiding schema failures
- ❌ Editing `fuel/*.md` as if it were code documentation — it is pipeline fuel
- ❌ Importing the orchestrator from library modules (breaks dependency direction)
- ❌ Treating a clean local run as proof: undefined names, import-direction
  breakage, and entry-point shape are caught by the gates in `tests/`
  (ruff F821/F811, import integrity, static analysis). Run the suite.
