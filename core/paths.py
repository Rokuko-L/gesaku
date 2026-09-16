"""Project path resolution and per-project file layout."""

import os
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


_root_dir = None

_project_name = None

def get_root_dir() -> Path:
    """Walk up from __file__ to locate the project root containing pyproject.toml or .env."""
    global _root_dir
    if _root_dir is None:
        current = Path(__file__).resolve().parent
        while True:
            if (current / "pyproject.toml").exists() or (current / ".env").exists():
                _root_dir = current
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
        if _root_dir is None:
            raise RuntimeError("Project root containing pyproject.toml or .env not found")
    return _root_dir

BASE_DIR = get_root_dir()

def set_project_name(name: str):
    """Set the active project name in global configuration memory."""
    global _project_name
    projects_root = (get_root_dir() / "projects").resolve()
    proposed_dir = (projects_root / name).resolve()
    try:
        is_rel = proposed_dir.is_relative_to(projects_root)
    except AttributeError:
        is_rel = proposed_dir == projects_root or projects_root in proposed_dir.parents
    if not is_rel or proposed_dir == projects_root:
        raise ValueError("Invalid project name: path isolation violation")
    _project_name = name
    os.environ["GESAKU_PROJECT"] = name

def get_project_name() -> str:
    """Retrieve the active project name, falling back to GESAKU_PROJECT env or 'default'."""
    global _project_name
    if _project_name is not None:
        return _project_name
    env_name = os.environ.get("GESAKU_PROJECT")
    if env_name:
        return env_name
    return "default"

def get_project_dir() -> Path:
    """Helper to return the current project's base directory."""
    projects_root = (get_root_dir() / "projects").resolve()
    proposed_dir = (projects_root / get_project_name()).resolve()
    try:
        is_rel = proposed_dir.is_relative_to(projects_root)
    except AttributeError:
        is_rel = proposed_dir == projects_root or projects_root in proposed_dir.parents
    if not is_rel or proposed_dir == projects_root:
        raise ValueError("Invalid project name: path isolation violation")
    return proposed_dir

def save_json_atomic(data, path: Path):
    """Atomically write JSON via .tmp file and rename, with cleanup on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise e


def save_registry(data: dict, path: Path):
    """Atomically write registry JSON (thin wrapper over save_json_atomic)."""
    save_json_atomic(data, path)

def get_chapters_dir() -> Path:
    d = get_project_dir() / "chapters"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_edit_logs_dir() -> Path:
    d = get_project_dir() / "edit_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_eval_logs_dir() -> Path:
    d = get_project_dir() / "eval_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_logs_dir() -> Path:
    d = get_project_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_llm_events_path() -> Path:
    """JSONL event log for LLM call telemetry (one JSON object per line)."""
    return get_project_dir() / "llm_events.jsonl"

def get_briefs_dir() -> Path:
    d = get_project_dir() / "briefs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_typeset_dir() -> Path:
    d = get_project_dir() / "typeset"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_active_genre_path() -> Path:
    return get_project_dir() / "active_genre.json"

def get_seed_path() -> Path:
    return get_project_dir() / "seed.txt"

def get_outline_path() -> Path:
    return get_project_dir() / "outline.md"

def get_state_path() -> Path:
    return get_project_dir() / "state.json"

def get_results_path() -> Path:
    return get_project_dir() / "results.tsv"

def get_registry_path() -> Path:
    return get_root_dir() / "projects" / "registry.json"

def get_world_path() -> Path:
    return get_project_dir() / "world.md"

def get_voice_path() -> Path:
    return get_project_dir() / "voice.md"

def get_characters_path() -> Path:
    return get_project_dir() / "characters.md"

def get_canon_path() -> Path:
    return get_project_dir() / "canon.md"

def get_manuscript_path() -> Path:
    return get_project_dir() / "manuscript.md"

def get_reviews_path() -> Path:
    return get_project_dir() / "reviews.md"

def get_arc_summary_path() -> Path:
    return get_project_dir() / "arc_summary.md"

def get_open_callbacks_path() -> Path:
    """Live ledger of prose-emergent micro-plants (distinct from outline debts)."""
    return get_project_dir() / "open_callbacks.json"

def get_premise_validation_path() -> Path:
    """Sidecar recording whether Chapter 1's premise beats validated."""
    return get_project_dir() / "premise_validation.json"

def get_plant_hygiene_path() -> Path:
    """Sidecar with sealed-term leaks + action-plant coverage findings."""
    return get_project_dir() / "plant_hygiene.json"

def get_retrofit_report_path() -> Path:
    """Post-reveal retrofit outcome + informational continuity scan."""
    return get_project_dir() / "retrofit_report.json"

def get_retry_feedback_path(ch: int) -> Path:
    """Per-chapter retry feedback handed to the next draft attempt."""
    return get_project_dir() / f"retry_feedback_ch{ch:02d}.txt"

def get_repetition_check_path() -> Path:
    """Structural-repetition sidecar written next to the chapters."""
    return get_chapters_dir() / "repetition_check.json"

def get_outline_roadmap_path() -> Path:
    """Pre-detail high-level roadmap (intermediate artifact of gen_outline)."""
    return get_project_dir() / ".outline_roadmap.md"

def get_outline_part1_path() -> Path:
    """First-half outline, kept so part 2 can refine rather than regenerate."""
    return get_project_dir() / ".outline_part1.md"

def get_outline_part2_path() -> Path:
    """Marker written when gen_outline_part2 finishes its polish pass.

    Chapter/tail presence alone cannot distinguish 'part 2 done' from 'part 1
    only', so the checkpoint reads this marker instead of guessing from prose.
    gen_outline deletes it whenever it rewrites outline.md, so a regenerated
    outline never inherits a stale done-marker.
    """
    return get_project_dir() / ".outline_part2.done"

def get_novel_title():
    """Retrieve novel title from state.json, resolving state path dynamically."""
    state_path = get_state_path()
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if "title" in state:
                return state["title"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    return "the novel"

def get_prompts_dir() -> Path:
    """Directory holding shared prompt templates (prompts/<name>.md)."""
    return get_root_dir() / "prompts"

def load_prompt(name: str) -> str:
    """Load a prompt template from prompts/<name>.md, cached per process.

    Templates keep the exact text of the inline constants they replaced,
    including {placeholder} and {{escaped}} braces, so existing
    .format() / format_prompt() call sites behave identically.
    """
    global _prompt_cache
    if _prompt_cache is None:
        _prompt_cache = {}
    if name not in _prompt_cache:
        path = get_prompts_dir() / f"{name}.md"
        _prompt_cache[name] = path.read_text(encoding="utf-8")
    return _prompt_cache[name]

_prompt_cache: dict | None = None

def format_prompt(template: str, **kwargs) -> str:
    """Format a template string by replacing both double-braced and single-braced placeholders."""
    for k, v in kwargs.items():
        template = template.replace(f"{{{{{k}}}}}", str(v))
        template = template.replace(f"{{{k}}}", str(v))
    return template
