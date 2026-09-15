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
for _p in (ROOT, ROOT / "scratch", WEBUI_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core import paths  # noqa: E402
from run_manager import RunManager  # noqa: E402


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def default_project() -> str:
    """Most recently touched project (state.json mtime) — the active one."""
    cands = []
    for d in (paths.get_root_dir() / "projects").iterdir():
        sf = d / "state.json"
        if d.is_dir() and sf.exists():
            cands.append((sf.stat().st_mtime, d.name))
    if not cands:
        raise HTTPException(404, "no projects with state.json under projects/")
    return max(cands)[1]


def resolve_name(name: str) -> str:
    """Validate a project name (path-isolation check); no existence requirement."""
    with _proj_lock:
        try:
            paths.set_project_name(name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    return name


def project_dir(name: str | None, must_exist: bool = True) -> tuple[str, Path]:
    """Validate the project name and return (name, dir)."""
    resolved = name or default_project()
    resolve_name(resolved)
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
run_manager = RunManager()


def run_state_fields(p: Path, state: dict) -> dict:
    return {
        "project": p.name,
        "phase": norm_phase(state),
        "iteration": state.get("iteration", 0),
        "foundationScore": state.get("foundation_score", 0) or 0,
        "loreScore": state.get("lore_score", 0) or 0,
        "chaptersTotal": state.get("chapters_total", 0) or 0,
        "chaptersDone": state.get("chapters_drafted", 0) or 0,
        "revisionCycle": state.get("revision_cycle", 0) or 0,
        **run_snapshot(p),
    }
