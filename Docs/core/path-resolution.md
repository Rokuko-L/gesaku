# Path Resolution & Project Isolation (`core/paths.py`)

All file access in gesaku goes through `paths.py`. Nothing may hardcode
`projects/<name>/...` strings — multi-project isolation depends on every path
being derived from the active project name at call time.

## Key Types

| Function | Purpose |
|---|---|
| `get_root_dir() -> Path` | Walks up from `__file__` to find `pyproject.toml`/`.env`. Cached in `_root_dir`; raises `RuntimeError` if missing. |
| `set_project_name(name)` | Sets the active project. **Validates path isolation**: resolved dir must be inside `projects/` (blocks `../`, `.`, absolute escapes). Also sets `GESAKU_PROJECT` env var so subprocesses inherit it. |
| `get_project_name() -> str` | Explicit set → `GESAKU_PROJECT` env → `"default"`. |
| `get_project_dir() -> Path` | `projects/<name>/`, re-validated on every call. |
| `save_json_atomic(data, path)` | Atomic JSON write: tmp file + `os.replace`, cleanup on failure. |
| `save_registry(data, path)` | Thin wrapper over `save_json_atomic` (kept for call-site clarity). |

## Folder Helpers (side effects: mkdir)

`get_chapters_dir`, `get_edit_logs_dir`, `get_eval_logs_dir`, `get_logs_dir`,
`get_briefs_dir`, `get_typeset_dir` — each returns `projects/<name>/<dir>/`,
creating it if missing.

## File Path Helpers (pure)

`get_active_genre_path`, `get_seed_path`, `get_outline_path`,
`get_state_path`, `get_results_path`, `get_registry_path`, `get_world_path`,
`get_voice_path`, `get_characters_path`, `get_canon_path`,
`get_manuscript_path`, `get_reviews_path`, `get_arc_summary_path`.

Plus:
- `get_novel_title()` — title from state.json, fallback `"the novel"`
- `load_prompt(name)` — cached read of `prompts/<name>.md`
- `format_prompt(template, **kwargs)` — replaces `{key}` and `{{key}}`

## Sidecar / Artifact Helpers (pure)

Every per-project artifact has a helper — never build the path inline:

| Helper | File |
|---|---|
| `get_premise_validation_path()` | `premise_validation.json` (ch1 premise-beat gate result) |
| `get_plant_hygiene_path()` | `plant_hygiene.json` (sealed-term leaks + action-plant coverage) |
| `get_retrofit_report_path()` | `retrofit_report.json` (post-reveal outcome + continuity scan) |
| `get_retry_feedback_path(ch)` | `retry_feedback_chNN.txt` (feedback for the next draft attempt) |
| `get_repetition_check_path()` | `chapters/repetition_check.json` |
| `get_outline_roadmap_path()` | `.outline_roadmap.md` (intermediate) |
| `get_outline_part1_path()` | `.outline_part1.md` (intermediate) |
| `get_outline_part2_path()` | `.outline_part2.done` (part-2 polish marker; written by `gen_outline_part2`, cleared by `gen_outline`) |
| `get_open_callbacks_path()` | `open_callbacks.json` (micro-plant ledger) |
| `get_llm_events_path()` | `llm_events.jsonl` (call telemetry) |

## Rules

1. New file types get a helper here, not inline `Path` math in callers.
2. Any user-derived name must pass through `set_project_name`.
3. Tests may patch internals directly: `paths._root_dir`,
   `paths._project_name` (see `tests/test_utils.py`).
4. Writes to shared JSON (registry, state, active_genre, eval logs, sidecars)
   must be atomic (`paths.save_json_atomic`).

Related: [llm-client.md](llm-client.md) ·
[../pipeline/state-and-git.md](../pipeline/state-and-git.md) ·
[../reference/test-suites.md](../reference/test-suites.md)
