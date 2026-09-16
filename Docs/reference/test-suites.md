# Test Suite Index

All suites run **offline** (no API key). Run everything with:

```
uv run python -m unittest discover -s tests -p "test_*.py"
```

`tests/` is the offline suite. A test that needs a real LLM belongs in the
E2E layer, not here.

## `unittest` modules (186 tests)

| Suite | Tests | Covers |
|---|---|---|
| `tests/test_refactor_smoke.py` | 21 | pipeline invariants: genre owns chapter count, best-novel peak tracking, foundation checkpoint skip, named timeout budgets, tolerance policy, LLM-output schemas, prompt loading, `run_tool` timeout semantics, callback/chapter coupling |
| `tests/test_canon_scoping.py` | 20 | sealed foundation views, As-of chapter filter, denylist terms, fail-closed malformed tags |
| `tests/test_micro_plants.py` | 16 | micro-plant store: add/harvest/expire, near-dup + plant↔harvest clustering |
| `tests/test_provider_llm.py` | 14 | wire format per dialect over `httpx.MockTransport` (URL, auth, payload, truncation parity) |
| `tests/test_scoring_guards.py` | 13 | keep/discard guards through the real foundation loop |
| `tests/test_utils.py` | 12 | path helpers, project state, atomic registry writes |
| `tests/test_llm_telemetry.py` | 11 | `llm_events.jsonl` emission + usage/stop-reason extraction from SSE streams and both dialects' key names |
| `tests/test_plant_coverage.py` | 10 | pre-reveal outline leak regex + action-plant coverage floor |
| `tests/test_mock_llm.py` | 8 | mock harness + validation-retry integration |
| `tests/test_epub_export.py` | 7 | EPUB build + structural validation (zip layout, OPF/spine/nav/NCX targets, escaping, stable identifier) and the export/CLI wiring |
| `tests/test_multi_project.py` | 6 | project-dir/state isolation, registry atomicity, path-traversal guard, from-scratch cleanup |
| `tests/test_run_manager.py` | 6 | bridge liveness: dead pid is not our run, stale `run.json` is dropped, tree-kill guards |
| `tests/test_utils_stress.py` | 6 | concurrency + traversal edge cases |
| `tests/test_webui_server.py` | 5 | bridge import smoke, `norm_phase`, `_mask`, `llm_event_view` (+ producer-source guard) |
| `tests/test_export_restore.py` | 4 | the export peak restore: extra chapters dropped, plant store restored, orphan store dropped |
| `tests/test_gatekeepers.py` | 4 | outline gatekeepers execute for real — drift verdicts block/pass, short books skip without LLM calls |
| `tests/test_path_contamination.py` | 4 | cross-project leakage, root cleanliness, registry placement |
| `tests/test_retrofit_gate.py` | 4 | retrofit coverage block + non-blocking continuity report |
| `tests/test_import_integrity.py` | 3 | AST-scans every import statement (incl. lazy function-level ones) resolves; enforces the `core <- foundation/pipeline` direction |
| `tests/test_revert_behavior.py` | 3 | results.tsv best-commit lookup + both revert branches, against a throwaway git repo |
| `tests/test_encoding_healing.py` | 2 | UTF-16/latin-1 self-heal |
| `tests/test_static_analysis.py` | 2 | ruff F821/F811 gate + single entry-point `main()` per entry file |
| `tests/test_tee_flush.py` | 2 | the Tee'd pipeline log is readable before close; a dead log handle cannot kill a run |
| `tests/test_json_repair.py` | 1 | damaged-JSON healing layers (one subtest per malformed payload) |

Some of these files also keep a `main()` so they can be run directly; the
`TestCase` is what CI discovers. `tests/` is scanned by
`test_import_integrity` along with `webui/`.

## Manual probes (require a live endpoint)

Not part of the offline suite — these hit the configured provider:

| Probe | Purpose |
|---|---|
| `tests/check_api.py` | full provider-resolution + request walk |
| `tests/check_api_minimal.py` | single request through `core.llm`'s own builder |
| `tests/probe_eval_consistency.py` | 5 parallel full evals to measure judge variance |

## Why the integrity scanner exists

Module-import smoke tests only execute top-level code, so a stale lazy
import inside a function survives every green suite — and if the surrounding
code has a broad `except`, it degrades into a silent no-op instead of
crashing (this actually happened: the tonal-drift gatekeeper was silently
dead after the core/ package move). `test_import_integrity.py` catches that
class statically; `test_gatekeepers.py` proves critical gates still *block*
when they should.

**Rule of thumb:** when you move/rename a module, run the integrity suite;
when you add or change a validation gate, add a behavioral test that asserts
the gate can fail. The static-analysis suite complements both: import
scanning verifies that *import statements* resolve, while ruff F821 verifies
that every *name used* is actually defined — these are different failure
classes (the process_notes `call_llm` NameError was only catchable by
the latter).

## E2E (requires API)

The 80-case E2E design (project isolation, CLI lifecycle, git-guard
containment, typesetting sandboxing) is recorded in
[archive/test-infra.md](archive/test-infra.md). It describes an older
snapshot of the suite and its runner (`pytest`), which the repo no longer
uses — treat it as design history, not current state.

## Convention

New offline tests go in `tests/test_*.py`. A test that needs a real LLM
belongs to the E2E layer or the probes above, not here.

A file matching `test_*.py` with **no `unittest.TestCase` is invisible to
CI** — `unittest discover` imports it and finds nothing. Five suites (path
isolation twice over, revert, JSON repair, encoding healing) sat in that
state: the checks existed and passed when run by hand, but never executed in
the suite. If a file keeps a `main()` for direct invocation, give it a
`TestCase` too.
