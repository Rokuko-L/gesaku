#!/usr/bin/env python3
"""Pipeline infrastructure: git plumbing, registry/state persistence,
subprocess helpers, score parsing, and shared constants.

Extracted from run_pipeline.py; the phase functions and CLI stay there.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core import paths
from core import _utf8
from core.llm import call_llm
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Tee:
    """Duplicate writes to both an original stream and a shared log file.

    Flushes the log file on every write. The wrapped handle is a plain
    `open()`, which is BLOCK-buffered by default — `reconfigure(
    line_buffering=True)` on the original stdout does not reach it, so
    without this flush the on-disk log sits at 0B for many minutes while
    the run is actively working (the whole point of the log is to be
    watchable live).
    """
    def __init__(self, fh, original):
        self.fh = fh
        self.original = original

    def write(self, data):
        # A log-file failure (disk full, closed handle, dead pipe) must never
        # kill the run — a closed file raises ValueError, not OSError.
        try:
            self.fh.write(data)
            self.fh.flush()
        except (OSError, ValueError):
            pass
        try:
            self.original.write(data)
            self.original.flush()
        except (OSError, ValueError):
            pass

    def flush(self):
        try:
            self.fh.flush()
        except (OSError, ValueError):
            pass
        try:
            self.original.flush()
        except (OSError, ValueError):
            pass

    def isatty(self):
        return self.original.isatty()

    def fileno(self):
        return self.original.fileno()

FOUNDATION_THRESHOLD = 7.5
FOUNDATION_PLATEAU_ITERS = 3   # consecutive non-improving iterations -> proceed with best docs


def foundation_plateau(score: float, best_score: float, stall_count: int):
    """Keep/discard decision for one foundation iteration.

    Returns (keep, new_best_score, new_stall_count). Improvement is strict:
    a tie counts as a stall, otherwise a judge returning flat scores (the
    motivating plateau case) never trips FOUNDATION_PLATEAU_ITERS.
    """
    if score > best_score:
        return True, score, 0
    return False, best_score, stall_count + 1

CHAPTER_THRESHOLD = 6.5

MAX_FOUNDATION_ITERS = 20

MAX_CHAPTER_ATTEMPTS = 5

INFRA_MAX_ATTEMPTS = 3      # separate budget for timeouts / empty-file infra failures

MAX_OUTLINE_ATTEMPTS = 5

MIN_REVISION_CYCLES = 3

MAX_REVISION_CYCLES = 6

PLATEAU_DELTA = 0.3

# Keep/discard tolerances — one documented policy, four distinct budgets.
# They differ on purpose: an LLM rewrite is high-variance, so a marginal
# regression is worth keeping (it usually carries improvements elsewhere);
# a deterministic cut pass should be score-neutral or it is a net loss.
REVISION_TOLERANCE = 0.8    # prose rewrites may regress this much and still keep
CUTS_TOLERANCE = 0.05       # mechanical cuts must be ~neutral to be kept
NEAR_CLEAN_MARGIN = 1.0     # draft this close to the gate + clean tics = keep, don't regen
FORCE_KEEP_MARGIN = 2.0     # below gate - this, skip the chapter rather than ship it

CHAPTERS_TOTAL = 24  # default; overridden by genre config at runtime

PHASE_ORDER = ["foundation", "drafting", "revision", "export"]


# ---------------------------------------------------------------------------
# Timeout policy (single owner for every subprocess + LLM budget)
# ---------------------------------------------------------------------------

# Subprocess caps for pipeline stages. Env-tunable; call sites ask for a
# named budget instead of inventing literals. LLM per-call timeouts live in
# core.llm.llm_timeout() under the same naming scheme.
TIMEOUT_SHORT = 300      # quick mechanical steps (sanitize, cuts, tex)
TIMEOUT_STANDARD = 900   # single generation passes (draft, revision)
TIMEOUT_LONG = 1800      # full-novel evals, chapter evals on slow proxies
TIMEOUT_XLONG = 3600     # foundation-scale generation blocks


def _env_timeout(name: str, default: int) -> int:
    try:
        val = int(float(os.getenv(name, "")))
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


def timeout_for(stage: str) -> int:
    """Named subprocess budget: short | standard | long | xlong."""
    table = {
        "short": _env_timeout("GESAKU_TIMEOUT_SHORT", TIMEOUT_SHORT),
        "standard": _env_timeout("GESAKU_TIMEOUT_STANDARD", TIMEOUT_STANDARD),
        "long": _env_timeout("GESAKU_TIMEOUT_LONG", TIMEOUT_LONG),
        "xlong": _env_timeout("GESAKU_TIMEOUT_XLONG", TIMEOUT_XLONG),
    }
    return table.get(stage, table["standard"])


# ---------------------------------------------------------------------------
# Chapter-count ownership (single source of truth)
# ---------------------------------------------------------------------------

def genre_chapters_total() -> int | None:
    """Chapter count from the genre config (canonical owner), or None."""
    from core import genre as genre_mod
    return genre_mod.chapters_total()


def resolve_chapters_total(state: dict) -> int:
    """Single owner: genre config once written, else state, else default."""
    genre_total = genre_chapters_total()
    if genre_total:
        if state.get("chapters_total") != genre_total:
            state["chapters_total"] = genre_total
            save_state(state)
        return genre_total
    if state.get("chapters_total", 0) > 0:
        return state["chapters_total"]
    return CHAPTERS_TOTAL


# ---------------------------------------------------------------------------
# Best-novel tracking (ship the peak, not the latest)
# ---------------------------------------------------------------------------

def record_novel_score(state: dict, score, commit: str | None = None) -> float | None:
    """Store the latest score and track the all-time best commit."""
    stored = store_novel_score(state, score)
    if stored is None:
        return state.get("novel_score")
    best = state.get("best_novel_score")
    try:
        best_f = float(best) if best is not None else None
    except (TypeError, ValueError):
        best_f = None
    if best_f is None or stored > best_f:
        state["best_novel_score"] = stored
        state["best_novel_commit"] = commit if commit else git_short_hash()
    save_state(state)
    return stored


def best_novel_checkpoint(state: dict) -> tuple[float | None, str | None]:
    """Return (best_score, best_commit) tracked in state."""
    return state.get("best_novel_score"), state.get("best_novel_commit")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def foundation_threshold() -> float:
    return _env_float("GESAKU_FOUNDATION_THRESHOLD", FOUNDATION_THRESHOLD)


def chapter_threshold() -> float:
    return _env_float("GESAKU_CHAPTER_THRESHOLD", CHAPTER_THRESHOLD)


def max_chapter_attempts() -> int:
    return _env_int("GESAKU_MAX_CHAPTER_ATTEMPTS", MAX_CHAPTER_ATTEMPTS)


def min_revision_cycles() -> int:
    return _env_int("GESAKU_MIN_REVISION_CYCLES", MIN_REVISION_CYCLES)


def max_revision_cycles() -> int:
    return _env_int("GESAKU_MAX_REVISION_CYCLES", MAX_REVISION_CYCLES)


def plateau_delta() -> float:
    return _env_float("GESAKU_PLATEAU_DELTA", PLATEAU_DELTA)


def revision_tolerance() -> float:
    return _env_float("GESAKU_REVISION_TOLERANCE", REVISION_TOLERANCE)


def cuts_tolerance() -> float:
    return _env_float("GESAKU_CUTS_TOLERANCE", CUTS_TOLERANCE)


def near_clean_margin() -> float:
    return _env_float("GESAKU_NEAR_CLEAN_MARGIN", NEAR_CLEAN_MARGIN)


def force_keep_margin() -> float:
    return _env_float("GESAKU_FORCE_KEEP_MARGIN", FORCE_KEEP_MARGIN)

def ensure_gitignore_projects():
    """Ensure root .gitignore contains a rule for projects/ to prevent nested-repo commits."""
    root = paths.get_root_dir()
    gi_path = root / ".gitignore"
    entry = "projects/"
    if gi_path.exists():
        content = gi_path.read_text(encoding="utf-8")
        lines = [l.strip() for l in content.splitlines()]
        if entry in lines:
            return  # already present
        gi_path.write_text(content.rstrip() + "\n" + entry + "\n", encoding="utf-8")
    else:
        gi_path.write_text(entry + "\n", encoding="utf-8")
    print(f"[git] Added '{entry}' to root .gitignore")

def ensure_project_git(project_dir: Path):
    """Initialize a git repo inside the project folder if not already present (idempotent)."""
    git_dir = project_dir / ".git"
    if git_dir.exists():
        return  # already initialized
    result = subprocess.run(
        ["git", "init", str(project_dir)],
        capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode == 0:
        print(f"[git] Initialized project repo at {project_dir}")
    else:
        print(f"[git] WARNING: git init failed: {result.stderr.strip()}")
    # Write a project-level .gitignore template
    proj_gi = project_dir / ".gitignore"
    if not proj_gi.exists():
        proj_gi.write_text("*.aux\n*.log\n*.toc\n*.out\n*.synctex.gz\n", encoding="utf-8")

def load_registry() -> dict:
    """Load the project registry JSON. Returns empty dict if not found."""
    reg_path = paths.get_registry_path()
    if reg_path.exists():
        try:
            return json.loads(reg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def update_registry(project_name: str, metadata: dict):
    """Atomically update registry.json with project metadata."""
    registry = load_registry()
    registry[project_name] = metadata
    paths.save_registry(registry, paths.get_registry_path())

def load_state() -> dict:
    """Load pipeline state from the active project's state.json, creating defaults if missing."""
    state_path = paths.get_state_path()
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    return default_state()

def default_state() -> dict:
    return {
        "phase": "foundation",
        "current_focus": "planning",
        "iteration": 0,
        "title": "Untitled",
        "foundation_score": 0.0,
        "lore_score": 0.0,
        "chapters_drafted": 0,
        "chapters_total": CHAPTERS_TOTAL,
        "novel_score": None,  # null = never scored; 0.0 is a real (failed) score
        "best_novel_score": None,
        "best_novel_commit": None,
        "revision_cycle": 0,
        "debts": [],
    }

def store_novel_score(state: dict, score):
    """Write a full-novel score into state. None / <=0 means "no usable score" — keep previous."""
    if score is None:
        return state.get("novel_score")
    try:
        score = float(score)
    except (TypeError, ValueError):
        return state.get("novel_score")
    if score <= 0:
        return state.get("novel_score")
    state["novel_score"] = score
    return score

def fmt_score(value) -> str:
    """Human-readable score for logs; None prints as ?."""
    return "?" if value is None else str(value)

def save_state(state: dict):
    """Atomically write state to the active project's state.json."""
    paths.save_json_atomic(state, paths.get_state_path())

def log_result(commit: str, phase: str, score, word_count: int,
               status: str, description: str):
    """Append a row to results.tsv in the active project directory."""
    results_file = paths.get_results_path()
    header = "commit\tphase\tscore\tword_count\tstatus\tdescription\n"
    if not results_file.exists():
        results_file.write_text(header, encoding="utf-8")
    elif results_file.stat().st_size == 0:
        results_file.write_text(header, encoding="utf-8")
    with open(results_file, "a", encoding="utf-8") as f:
        f.write(f"{commit}\t{phase}\t{score}\t{word_count}\t{status}\t{description}\n")

def banner(text: str, char: str = "=", width: int = 60):
    """Print a visible phase/step banner."""
    print(f"\n{char * width}")
    print(f"  {text}")
    print(f"{char * width}")

def step(text: str):
    """Print a step indicator."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] {text}")

def run_tool(cmd: str, timeout: int = 600, check: bool = False, cwd: str = None) -> subprocess.CompletedProcess:
    """
    Run a tool as a subprocess, capturing output.
    Uses shell=False with shlex.split for argument safety.
    Returns CompletedProcess; never raises unless check=True.
    """
    step(f"RUN: {cmd}")
    try:
        cmd_norm = cmd.replace("\\", "/")
        effective_cwd = cwd if cwd is not None else str(paths.get_root_dir())
        result = subprocess.run(
            shlex.split(cmd_norm), shell=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, cwd=effective_cwd,
        )
        if result.returncode != 0:
            print(f"    WARN: exit code {result.returncode}")
        stderr_preview = (result.stderr or "")[:2000]
        if stderr_preview:
            print(f"    stderr: {stderr_preview}")
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr)
        return result
    except subprocess.TimeoutExpired as e:
        print(f"    ERROR: timed out after {timeout}s")
        if check:
            raise
        # Callers that asked for fail-fast semantics must not receive a
        # fabricated success-shaped result: rc=-1 with empty stdout used to
        # flow into parse_score() and surface as a bogus ValueError far from
        # the real cause.
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="TIMEOUT")

def uv_run(script: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """Shorthand for running a Python script from project root. Fails fast."""
    return run_tool(f"\"{sys.executable}\" {script}", timeout=timeout, check=True)

def git_add_commit(message: str) -> str:
    """Stage all changes and commit. Returns short hash or empty string."""
    project_dir = paths.get_project_dir()
    run_tool("git add -A", cwd=str(project_dir))
    status_result = run_tool("git status --porcelain", cwd=str(project_dir))
    if status_result.stdout.strip():
        result = run_tool(f'git commit -m "{message}"', cwd=str(project_dir))
        if result.returncode == 0:
            hash_result = run_tool("git rev-parse --short HEAD", cwd=str(project_dir))
            commit_hash = hash_result.stdout.strip()
            step(f"GIT COMMIT: {commit_hash} — {message}")
            return commit_hash
    step("GIT: nothing to commit or commit failed")
    return ""

def git_reset_hard(ref: str = "HEAD~1"):
    """Hard reset to discard bad changes.

    Preserves legitimately-appended canon.md entries (they ride along with the
    next commit after a reset and would otherwise be silently lost), and spares
    the timestamped eval/edit/brief logs from `git clean` so downstream stages
    don't silently no-op on freshly-written review/cuts artifacts.
    """
    step(f"GIT RESET: {ref}")
    project_dir = paths.get_project_dir()

    # Preserve uncommitted canon.md appends (e.g. from a successful chapter eval
    # that hasn't been committed yet when this reset fires).
    canon_status = run_tool("git status --porcelain -- canon.md", cwd=str(project_dir))
    if any(line.startswith((" M", "M ", "MM")) for line in canon_status.stdout.splitlines()):
        run_tool("git add canon.md", cwd=str(project_dir))
        git_commit_staged("canon: preserve pending append before reset")

    run_tool(f"git reset --hard {ref}", cwd=str(project_dir))
    # Clean untracked files/directories to prevent cross-iteration contamination,
    # but never delete the timestamped artifact logs the pipeline depends on.
    run_tool(
        "git clean -fd -e eval_logs -e edit_logs -e briefs -e logs -e repetition_check.json",
        cwd=str(project_dir),
    )

def git_commit_staged(message: str) -> str:
    """Commit already-staged changes. Returns short hash or empty string."""
    project_dir = paths.get_project_dir()
    status_result = run_tool("git status --porcelain", cwd=str(project_dir))
    # Check if there are staged changes (staged changes start with non-space in porcelain status)
    staged = False
    for line in status_result.stdout.splitlines():
        if line and not line.startswith(" ") and not line.startswith("?"):
            staged = True
            break
    if staged:
        result = run_tool(f'git commit -m "{message}"', cwd=str(project_dir))
        if result.returncode == 0:
            hash_result = run_tool("git rev-parse --short HEAD", cwd=str(project_dir))
            commit_hash = hash_result.stdout.strip()
            step(f"GIT COMMIT STAGED: {commit_hash} — {message}")
            return commit_hash
    step("GIT: nothing staged to commit or commit failed")
    return ""

def get_historical_best_for_chapter(ch_num: int) -> tuple[float, str]:
    """
    Parses results.tsv to find the highest score kept for this chapter.
    Returns: (best_score, commit_hash)
    """
    results_path = paths.get_results_path()
    if not results_path.exists():
        return 0.0, "HEAD"
        
    best_score = 0.0
    best_commit = "HEAD"
    target_phases = {f"ch{ch_num:02d}", f"rev-ch{ch_num:02d}", f"rev-ch{ch_num:02d}-review"}
    
    try:
        with open(results_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= 1:
            return 0.0, "HEAD"
            
        headers = lines[0].strip().split("\t")
        commit_idx = headers.index("commit")
        phase_idx = headers.index("phase")
        score_idx = headers.index("score")
        status_idx = headers.index("status")
        
        for line in lines[1:]:
            parts = line.strip().split("\t")
            if len(parts) > max(commit_idx, phase_idx, score_idx, status_idx):
                phase = parts[phase_idx]
                status = parts[status_idx]
                commit = parts[commit_idx]
                if phase in target_phases and status in ("keep", "forced") and commit != "reverted":
                    try:
                        score = float(parts[score_idx])
                        if score > best_score:
                            best_score = score
                            best_commit = commit
                    except ValueError:
                        continue
    except Exception as e:
        step(f"Warning: Failed to parse historical best for ch {ch_num}: {e}")
        
    return best_score, best_commit

def git_short_hash() -> str:
    """Get current HEAD short hash."""
    r = run_tool("git rev-parse --short HEAD", cwd=str(paths.get_project_dir()))
    return r.stdout.strip() if r.returncode == 0 else "unknown"

from pipeline.scores import (  # noqa: E402
    _chapter_num_key, count_chapter_files, count_words_in_chapters,
    parse_lore_score, parse_score, parse_score_any,
)


def get_total_chapters(state: dict) -> int:
    """Deprecated shim — use resolve_chapters_total(state).

    Kept so stage scripts keep importing; the genre config is the owner.
    """
    return resolve_chapters_total(state)


def process_notes(notes_input, genre):
    """Process user notes into seed.txt and return the string for gen_genre_framework.

    Resolves file-or-string input, then branches on word count:
      Short (<300w):     LLM expand → seed.txt gets expansion, returns original
      Goldilocks (300-1500w):        seed.txt gets notes, returns original
      Massive (>1500w):  LLM summarize → seed.txt gets full doc, returns summary

    Returns None if no notes_input provided.
    """
    if not notes_input:
        return None

    notes_str = str(notes_input)
    # Only treat input as a filesystem path when it can plausibly be one.
    # Long inline premises crash Path.exists() with [Errno 36] ENAMETOOLONG
    # on Linux (255-byte filename limit).
    looks_like_path = len(notes_str) < 260 and "\n" not in notes_str
    if looks_like_path and Path(notes_str).is_file():
        notes = Path(notes_str).read_text(encoding="utf-8")
        step(f"Read notes from file: {notes_str}")
    else:
        notes = notes_str

    word_count = len(notes.split())
    genre_str = genre or "the specified genre"

    banner(f"PROCESSING NOTES ({word_count} words)", "-")

    # seed.txt lives in the project directory
    seed_file = paths.get_seed_path()

    if word_count < 300:
        step(f"Notes too short ({word_count}w). Expanding to ~500 words via LLM...")
        expanded = call_llm(
            prompt=(
                f"The user has provided a very brief premise for a {genre_str} novel:\n\n"
                f"'{notes}'\n\n"
                f"Expand this into a dense, rich 500-word story document. "
                f"Establish a compelling core conflict, hint at the worldbuilding/setting, "
                f"and outline the protagonist's main flaw and goal. "
                f"Make it highly specific and creative."
            ),
            model_key="judge",
            max_tokens=2000,
            temperature=0.8,
            timeout_role="short",
        )
        seed_file.write_text(expanded, encoding="utf-8")
        step(f"seed.txt written ({len(expanded.split())}w, expanded from {word_count})")
        return notes

    if word_count <= 1500:
        step(f"Notes are a good size ({word_count}w). Writing directly to seed.txt.")
        seed_file.write_text(notes, encoding="utf-8")
        return notes

    step(f"Notes are very long ({word_count}w). Summarizing to ~500 words for genre framework...")
    summary = call_llm(
        prompt=(
            f"The user has provided a massive document ({word_count} words) for a {genre_str} novel. "
            f"Extract a dense 500-word summary of the core premise, genre, main characters, "
            f"and central conflict. Do not write a story, just extract the core DNA.\n\n"
            f"=== DOCUMENT ===\n{notes}"
        ),
        model_key="judge",
        max_tokens=2000,
        temperature=0.3,
        timeout_role="short",
    )
    seed_file.write_text(notes, encoding="utf-8")
    step(f"seed.txt written with full {word_count}w doc. Summary ({len(summary.split())}w) sent to genre framework.")
    return summary
