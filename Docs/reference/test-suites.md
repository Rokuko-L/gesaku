# Test Suite Index

All suites run **offline** (no API key). Run everything with:

```
uv run python -m unittest discover -s tests -p "test_*.py"
```

`tests/` is the offline suite. A test that needs a real LLM belongs in the
E2E layer, not here.

## `unittest` modules (141 tests)

| Suite | Tests | Covers |
|---|---|---|
| `tests/test_refactor_smoke.py` | 21 | pipeline invariants: genre owns chapter count, best-novel peak tracking, foundation checkpoint skip, named timeout budgets, tolerance policy, LLM-output schemas, prompt loading, `run_tool` timeout semantics, callback/chapter coupling |
| `tests/test_canon_scoping.py` | 20 | sealed foundation views, As-of chapter filter, denylist terms |
| `tests/test_micro_plants.py` | 16 | micro-plant store: add/harvest/expire, near-dup + plant↔harvest clustering |
| `tests/test_provider_llm.py` | 14 | wire format per dialect over `httpx.MockTransport` (URL, auth, payload, truncation parity) |
| `tests/test_scoring_guards.py` | 13 | keep/discard guards through the real foundation loop |
| `tests/test_utils.py` | 12 | path helpers, project state |
| `tests/test_plant_coverage.py` | 10 | pre-reveal outline leak regex + action-plant coverage floor |
| `tests/test_mock_llm.py` | 8 | mock harness + validation-retry integration |
| `tests/test_utils_stress.py` | 6 | concurrency + traversal edge cases |
| `tests/test_gatekeepers.py` | 4 | outline gatekeepers execute for real — drift verdicts block/pass, short books skip without LLM calls |
| `tests/test_llm_telemetry.py` | 4 | `llm_events.jsonl` emission |
| `tests/test_retrofit_gate.py` | 4 | retrofit coverage block + non-blocking continuity report |
| `tests/test_webui_server.py` | 3 | bridge import smoke + pure helpers (`norm_phase`, `_mask`) |
| `tests/test_import_integrity.py` | 2 | AST-scans every import statement (incl. lazy function-level ones) resolves |
| `tests/test_static_analysis.py` | 2 | ruff F821/F811 gate + single entry-point `main()` per entry file |
| `tests/test_tee_flush.py` | 2 | the Tee'd pipeline log is readable before close; a dead log handle cannot kill a run |

## Script-style suites

Exit non-zero on failure and print `[PASS]/[FAIL]` lines rather than using
`unittest`:

| Suite | Covers |
|---|---|
| `tests/test_json_repair.py` | damaged-JSON healing layers |
| `tests/test_encoding_healing.py` | UTF-16/latin-1 self-heal |
| `tests/test_multi_project.py` | registry isolation, from-scratch cleanup |
| `tests/test_path_contamination.py` | cross-project leakage, root cleanliness |
| `tests/test_revert_behavior.py` | historical-best lookup + revert targeting |

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
