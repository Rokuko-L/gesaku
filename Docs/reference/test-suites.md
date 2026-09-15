# Test Suite Index

All suites run **offline** (no API key). `unittest`-based suites can also run
via discovery: `uv run python -m unittest discover -s scratch -p "test_*.py"`.

| Suite | Covers | Runner |
|---|---|---|
| `scratch/test_utils.py` | path helpers, project state (12 tests) | unittest |
| `scratch/test_utils_stress.py` | concurrency + traversal edge cases (6 tests) | unittest |
| `scratch/test_json_repair.py` | damaged-JSON healing layers | script |
| `scratch/test_encoding_healing.py` | UTF-16/latin-1 self-heal | script |
| `scratch/test_multi_project.py` | registry isolation, from-scratch cleanup | script |
| `scratch/test_path_contamination.py` | cross-project leakage, root cleanliness | script |
| `scratch/test_mock_llm.py` | mock harness + validation-retry integration (8 tests) | unittest |
| `scratch/test_import_integrity.py` | AST-scans every import statement (incl. lazy function-level ones) resolves; utils.py stays deleted (2 tests) | unittest |
| `scratch/test_gatekeepers.py` | outline gatekeepers execute for real — drift verdicts block/pass, short books skip without LLM calls (4 tests) | unittest |
| `scratch/test_static_analysis.py` | ruff pyflakes gate: no undefined names (F821), no accidental redefinitions (F811) across the repo (1 test) | unittest |
| `scratch/test_canon_scoping.py` | sealed foundation views, As-of chapter filter, denylist terms | unittest |
| `scratch/test_plant_coverage.py` | pre-reveal outline leak regex + action-plant coverage floor | unittest |
| `scratch/test_retrofit_gate.py` | retrofit coverage block + non-blocking continuity report | unittest |

Script-style suites exit non-zero on failure and print `[PASS]/[FAIL]` lines.

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

See [test-infra.md](test-infra.md) for the full E2E infrastructure:
project isolation, CLI integration, lifecycle, git-guard containment,
typesetting sandboxing.

## Convention

New offline tests go in `scratch/test_*.py`. A test that needs a real LLM
belongs to the E2E layer, not here.
