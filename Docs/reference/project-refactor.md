# Project: Gesaku Pipeline Refactoring (COMPLETED — historical record)

> Status: all milestones shipped. Note that `utils.py` has since been split
> into `core/` modules (`paths.py`, `llm.py`, ...) — wherever this document
> says "utils", read the corresponding concern module. Live contracts:
> [../core/path-resolution.md](../core/path-resolution.md).

## Architecture
Gesaku is a fully automated novel generation pipeline. The goal of this refactoring is to support multi-project isolation so that different novels/sessions can run concurrently under `projects/<project_name>/`.

- **Registry System**: `projects/registry.json` tracks project sessions atomically.
- **Path Resolution**: `utils.py` acts as the dynamic path resolution provider, determining folders and files dynamically based on active configuration.
- **Script Routing**: Individual generator scripts call `utils` path functions rather than hardcoded global paths.
- **Sandboxed Compilation**: Typesetting via Tectonic runs in a separate subprocess `cwd` (`projects/<project_name>/typeset/`).
- **Git Protection**: The root `.gitignore` ignores `projects/`, while each project directory has an independent Git repository.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | E2E Test Suite | Create E2E testing scripts under `tests/` | none | IN_PROGRESS (Subagent: 802f9463-e9c1-460f-bdbf-b2de0bc722af) |
| 2 | Path & Config | Refactor `utils.py` with dynamic path helpers, project state, and atomic save | none | IN_PROGRESS (Subagent: 7a416a18-a3b9-4d25-aacc-3d2e39cb779e) |
| 3 | Pipeline Orchestration | Refactor `run_pipeline.py` with registry management, CLI args, and Git guards | M2 | IN_PROGRESS (Subagent: 7a416a18-a3b9-4d25-aacc-3d2e39cb779e) |
| 4 | Script Routing | Refactor other pipeline scripts and typeset helper | M2, M3 | IN_PROGRESS (Subagent: 7a416a18-a3b9-4d25-aacc-3d2e39cb779e) |
| 5 | Validation & Hardening | Execute E2E tests, audit, check contamination, and run Forensic Auditor | M1, M2, M3, M4 | PLANNED |

## Code Layout
- `utils.py`: Path helpers, active project state, Anthropic API interface.
- `run_pipeline.py`: Main entry point and lifecycle orchestrator.
- `tests/`: Verification test suites.
- `projects/`: Root folder for all isolated project directories.
  - `registry.json`: Session registry list.
  - `<project_name>/`: Specific project workspace folder.
    - `.git/`: Project-level Git directory.
    - `state.json`: Saved pipeline execution state.
    - `chapters/`: Drafted chapters (`ch_*.md`).
    - `edit_logs/`: Logs for edits.
    - `eval_logs/`: Evaluation logs.
    - `briefs/`: Generation briefs.
    - `typeset/`: Typesetting build outputs.

## Interface Contracts

### utils ↔ pipeline & scripts
- `utils.get_root_dir() -> Path`
  - Walks up parents of `__file__` to find `pyproject.toml` or `.env`. Raises `RuntimeError` if missing.
- `utils.set_project_name(name: str)`
  - Explicitly sets the active project name in global or session-level configuration memory.
- `utils.get_project_name() -> str`
  - Gets the active project name, falling back to `GESAKU_PROJECT` env var, and then defaults to `"default"`.
- `utils.save_registry(data: dict, path: Path)`
  - Atomically writes registry JSON data via `.tmp` file and rename, with cleanup on JSON serialization failure.
- `utils.get_chapters_dir() -> Path`
- `utils.get_edit_logs_dir() -> Path`
- `utils.get_eval_logs_dir() -> Path`
- `utils.get_briefs_dir() -> Path`
- `utils.get_typeset_dir() -> Path`
  - Dynamic folder path getters. Ensure that parent/target directories exist.
- `utils.get_outline_path() -> Path`
- `utils.get_state_path() -> Path`
- `utils.get_results_path() -> Path`
- `utils.get_registry_path() -> Path`
- `utils.get_world_path() -> Path`
- `utils.get_voice_path() -> Path`
- `utils.get_characters_path() -> Path`
- `utils.get_canon_path() -> Path`
- `utils.get_manuscript_path() -> Path`
- `utils.get_reviews_path() -> Path`
- `utils.get_arc_summary_path() -> Path`
  - Pure function path helpers returning a `Path` object without side effects.
