"""Shared helpers for the webui bridge (project resolution, state).

Project resolution goes through paths.py's global set_project_name under a
lock — this is a single-user local console, not a per-request context.
"""

import json
import threading
from datetime import datetime, timezone

from fastapi import HTTPException

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = Path(__file__).resolve().parent
for _p in (ROOT, WEBUI_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core import paths  # noqa: E402
from run_manager import run_manager  # noqa: E402


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def default_project() -> str:
    """Most recently touched project (state.json mtime) — the active one."""
    cands = []
    projects_dir = paths.get_root_dir() / "projects"
    if projects_dir.is_dir():
        for d in projects_dir.iterdir():
            sf = d / "state.json"
            if d.is_dir() and sf.exists():
                cands.append((sf.stat().st_mtime, d.name))
    if not cands:
        raise HTTPException(404, "no projects with state.json under projects/")
    return max(cands)[1]


def llm_event_view(e: dict) -> dict:
    """Map one llm_events.jsonl record (snake_case) to the API shape.

    Single owner of that mapping: the history endpoint and the SSE feed must
    expose the same camelCase keys or live rows render blank while the same
    call looks correct after a reload.
    """
    return {
        "ts": e.get("ts"),
        "modelKey": e.get("model_key"),
        "model": e.get("model"),
        "ok": e.get("ok"),
        "attempt": e.get("attempt"),
        "tokensIn": e.get("tokens_in"),
        "tokensOut": e.get("tokens_out"),
        "durationMs": e.get("duration_ms"),
        "stopReason": e.get("stop_reason"),
        "promptChars": e.get("prompt_chars"),
        "responseChars": e.get("response_chars"),
        "promptHead": e.get("prompt_head"),
        "error": e.get("error"),
    }


def project_dir(name: str | None, must_exist: bool = True) -> tuple[str, Path]:
    """Validate the project name and return (name, dir).

    Resolution runs entirely under the lock: `get_project_dir()` reads the same
    module global that `set_project_name()` writes, so releasing the lock
    between them lets a concurrent request for another project swap the global
    and send this request into the wrong directory.
    """
    resolved = name or default_project()
    with _proj_lock:
        try:
            paths.set_project_name(resolved)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        p = paths.get_project_dir()
    if must_exist and not (p / "state.json").exists():
        raise HTTPException(404, f"unknown project: {resolved}")
    return resolved, p


def load_state(p: Path) -> dict:
    return json.loads((p / "state.json").read_text(encoding="utf-8"))


def norm_phase(state: dict) -> str:
    if state.get("current_focus") == "done":
        return "idle"
    phase = state.get("phase", "idle") or "idle"
    return "export" if phase.startswith("complete") else phase


def run_snapshot(p: Path) -> dict:
    st = run_manager.status(p)
    return {
        "running": st["running"],
        "pid": st.get("pid"),
        "runStartedAt": st.get("startedAt"),
        "exitCode": st.get("exitCode"),
    }

_proj_lock = threading.Lock()


def run_state_fields(p: Path, state: dict) -> dict:
    return {
        "project": p.name,
        "phase": norm_phase(state),
        # `phase` is "idle" both for a project that never ran and for one that
        # finished — the stepper needs that collapse, but the UI must not
        # present a completed novel as "idle". This is the disambiguator.
        "finished": state.get("current_focus") == "done",
        "iteration": state.get("iteration", 0),
        "novelScore": state.get("novel_score"),
        "bestNovelScore": state.get("best_novel_score"),
        "foundationScore": state.get("foundation_score", 0) or 0,
        "loreScore": state.get("lore_score", 0) or 0,
        "chaptersTotal": state.get("chapters_total", 0) or 0,
        "chaptersDone": state.get("chapters_drafted", 0) or 0,
        "revisionCycle": state.get("revision_cycle", 0) or 0,
        **run_snapshot(p),
    }
