---
name: gesaku-style
description: Applies the gesaku repo's Python coding style and structural rules. Use this whenever writing or reviewing code in D:\Tugas\LLM\autonovel.
---

# Gesaku Coding Style

## Module Layout (where new code goes)

```
paths.py / llm.py / outline.py / textstats.py / novel_tex.py   core concerns
validation.py                                                  LLM output schemas
mock_llm.py                                                    test harness
pipeline_infra.py                                              git/registry/state plumbing
run_pipeline.py                                                phases + CLI only
prompts/*.md                                                   static prompt templates
tests/test_*.py                                              offline tests
```

- **Import from the concern module directly** (`from llm import call_llm`,
  `import paths`) — there is no umbrella module. New shared helpers go in the
  matching concern module; a genuinely new concern gets a new module.
- One generation task = one `gen_*.py` script, mirroring the pipeline stage.

## Hard Rules

1. **Path access via `paths.py` only.** No hardcoded `projects/<name>/...`
   strings. Any user-derived project name must pass through
   `set_project_name` (path-isolation check).
2. **LLM JSON → Pydantic, not dict-hope.** Define a model in
   `validation.py`, parse with `parse_validated(Model, text)`, and on
   `OutputValidationError` feed `.feedback` into the self-correction retry.
   Never re-implement regex extraction for JSON a judge already returns.
3. **Static prompts → `prompts/*.md`.** Load with `paths.load_prompt(name)`.
   Templates keep `{placeholder}` / `{{escaped}}` braces exactly as the old
   inline literals did, so `.format()` call sites are unchanged.
4. **Atomic writes**: tmp file + `os.replace`, cleanup on failure (see
   `save_registry`). Never write state/registry in place.
5. **Tests run offline.** Use `mock_llm.MockLLM`; install before or after
   importing pipeline modules (it rebinds import-time references). A test
   suite that needs an API key belongs in E2E, not `tests/test_*`.

## Conventions

| Convention | Rule |
|---|---|
| Python | 3.12+, run everything through `uv run` |
| Encoding | explicit `encoding="utf-8"` on every read_text/write_text/open |
| Style | plain functions over classes; module-level `_globals` with lazy init |
| Docstrings | public functions only, one line where possible |
| Errors | raise with actionable messages; no bare `except Exception: pass` in new code |
| Retries | LLM calls go through `call_llm`'s backoff; phase-level retries belong to `run_pipeline.py` phases |
| Comments | non-obvious logic only; the code is the source of truth |

## What to Avoid

- ❌ New monoliths: if a file passes ~500 lines, split by concern
- ❌ Regex-parsing judge output that is already JSON
- ❌ Silent score fallbacks (`return -1.0`) hiding schema failures
- ❌ Editing CRAFT.md / ANTI-SLOP.md / voice.md as if they were code docs — they are pipeline fuel
- ❌ Importing `run_pipeline` from library modules (keeps dependency direction: scripts → infra/core)
