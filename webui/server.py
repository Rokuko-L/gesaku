"""FastAPI bridge — serves real project data to the webui operator console.

Implements the shapes declared in webui/frontend/src/api/contract.js on top of
whatever the pipeline has already written to projects/<name>/ (state.json,
results.tsv, eval_logs, briefs, edit_logs, chapters), plus run lifecycle:
POST /api/projects launches run_pipeline.py through run_manager, and
GET /api/stream is an SSE feed (state snapshots, log tail, llm events).

Run from the repo root:
    uv run uvicorn server:app --app-dir webui --port 8600

The vite dev server proxies /api -> http://127.0.0.1:8600 (vite.config.js).
Single-user local console: project resolution goes through paths.py's global
set_project_name under a lock, not a per-request context.
"""

import asyncio
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = Path(__file__).resolve().parent
for _p in (ROOT, WEBUI_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core import paths  # noqa: E402
import fixtures as gen  # noqa: E402
from pipeline import pipeline_infra  # noqa: E402
from run_manager import SEEDS_DIR  # noqa: E402

from deps import (  # noqa: E402
    _iso, default_project, load_state, norm_phase, project_dir,
    resolve_name, run_manager, run_snapshot,
)
from routes import graph as routes_graph  # noqa: E402
from routes import settings as routes_settings  # noqa: E402
from routes import stream as routes_stream  # noqa: E402

app = FastAPI(title="gesaku operator console", docs_url="/api/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(routes_graph.router)
app.include_router(routes_settings.router)
app.include_router(routes_stream.router)


# ---------------------------------------------------------------- projects


@app.get("/api/projects")
def list_projects():
    name, p = project_dir(None)
    items = gen.gen_projects(load_state(p), p)
    for item in items:
        _, ip = project_dir(item["name"])
        sf = ip / "state.json"
        item["updatedAt"] = _iso(sf.stat().st_mtime) if sf.exists() else None
        item["running"] = run_manager.status(ip)["running"]
    items.sort(key=lambda x: x.get("updatedAt") or "", reverse=True)
    return items


class CreateProject(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    genre: str = ""
    notes: str = ""           # raw premise text (written to a seed file)…
    notesPath: str = ""       # …or a path to an existing file (takes precedence)
    chapters: int = Field(default=24, ge=4, le=200)
    wordsPerChapter: int = Field(default=3000, ge=500, le=8000)
    revisionCycles: int = Field(default=3, ge=0, le=6)
    perspective: str = Field(default="third_person", pattern="^(first_person|third_person)$")
    proseMode: str = Field(
        default="",
        pattern="^(|first_intimate|first_voicey|third_close|third_scene)$",
    )
    fromScratch: bool = True


@app.post("/api/projects")
def create_project(req: CreateProject):
    """Validate wizard input and launch run_pipeline.py for a new project."""
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "project name is required")
    try:
        resolve_name(name)
    except HTTPException:
        raise HTTPException(400, f"invalid project name: {name!r}") from None
    if not req.genre.strip():
        raise HTTPException(
            400, "genre is required for a fresh project — the pipeline's sanity check exits without it")

    p = paths.get_project_dir()
    if req.fromScratch and p.exists() and any(p.iterdir()):
        raise HTTPException(
            409, f"project dir already has content: {name} — pick another name or clear it first")

    notes_arg = req.notesPath.strip()
    if not notes_arg and req.notes.strip():
        SEEDS_DIR.mkdir(parents=True, exist_ok=True)
        seed = SEEDS_DIR / f"{name}.txt"
        seed.write_text(req.notes, encoding="utf-8")
        notes_arg = str(seed)

    cli = [
        "--project", name, "--from-scratch",
        "--genre", req.genre.strip(),
        "--chapters", str(req.chapters),
        "--words-per-chapter", str(req.wordsPerChapter),
        "--revision-cycles", str(req.revisionCycles),
        "--perspective", req.perspective,
    ]
    if req.proseMode:
        cli += ["--prose-mode", req.proseMode]
    if notes_arg:
        cli += ["--notes", notes_arg]

    p.mkdir(parents=True, exist_ok=True)
    # Persist launch knobs so a later resume can recover genre if foundation
    # dies before writing active_genre.json.
    try:
        paths.save_json_atomic(
            {
                "genre": req.genre.strip(),
                "chapters": req.chapters,
                "wordsPerChapter": req.wordsPerChapter,
                "revisionCycles": req.revisionCycles,
                "perspective": req.perspective,
                "proseMode": req.proseMode,
                "savedAt": datetime.now(timezone.utc).isoformat(),
            },
            p / "launch.json",
        )
    except OSError:
        pass
    try:
        meta = run_manager.launch(p, cli)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "project": name, **run_snapshot(p), "logPath": meta.get("logPath")}


@app.post("/api/run/stop")
def run_stop(project: str | None = Query(None)):
    _, p = project_dir(project)
    stopped = run_manager.stop(p)
    return {"ok": True, "stopped": stopped}


class StartRun(BaseModel):
    project: str | None = None
    revisionCycles: int | None = Field(default=None, ge=0, le=6)
    genre: str = ""
    chapters: int | None = Field(default=None, ge=4, le=200)
    wordsPerChapter: int | None = Field(default=None, ge=500, le=8000)
    perspective: str | None = None
    proseMode: str | None = None


def _load_launch(p: Path) -> dict:
    launch = p / "launch.json"
    if not launch.exists():
        return {}
    try:
        data = json.loads(launch.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _infer_genre(p: Path) -> str:
    """Genre for a resume when active_genre.json was never written (crash before foundation finished)."""
    g = (_load_launch(p) or {}).get("genre") or ""
    if str(g).strip():
        return str(g).strip()
    for src in (p / "seed.txt", SEEDS_DIR / f"{p.name}.txt"):
        if not src.exists():
            continue
        try:
            text = src.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = re.search(r"(?im)^Genre:\s*(.+)$", text)
        if m:
            return m.group(1).strip()
    return ""


@app.post("/api/run/start")
def run_start(req: StartRun | None = None):
    """Resume an existing project's pipeline (no --from-scratch).

    state.json picks up the current phase. Genre/chapter shape come from
    launch.json (saved at create) when regenerating a missing active_genre.json.
    An existing active_genre.json is never clobbered by a plain resume.
    """
    name, p = project_dir(req.project if req else None)
    if run_manager.status(p)["running"]:
        raise HTTPException(409, f"project '{name}' already has a running pipeline")

    launch = _load_launch(p)
    cli = ["--project", name]
    cycles = (req.revisionCycles if req else None)
    if cycles is not None:
        cli += ["--revision-cycles", str(cycles)]

    has_genre_file = (p / "active_genre.json").exists() or (paths.get_root_dir() / "active_genre.json").exists()

    if not has_genre_file:
        genre = ((req.genre if req else "") or "").strip() or _infer_genre(p)
        if not genre:
            raise HTTPException(
                400,
                f"project '{name}' has no active_genre.json and no genre on file — "
                "pass genre in the request or add a 'Genre:' line to seed.txt",
            )
        cli += ["--genre", genre]
        chapters = req.chapters if req and req.chapters else launch.get("chapters")
        wpc = req.wordsPerChapter if req and req.wordsPerChapter else launch.get("wordsPerChapter")
        perspective = (req.perspective if req and req.perspective is not None else launch.get("perspective")) or ""
        prose_mode = (req.proseMode if req and req.proseMode is not None else launch.get("proseMode")) or ""
        if chapters:
            cli += ["--chapters", str(int(chapters))]
        if wpc:
            cli += ["--words-per-chapter", str(int(wpc))]
        if perspective:
            cli += ["--perspective", str(perspective)]
        if prose_mode:
            cli += ["--prose-mode", str(prose_mode)]
        # Persist so the next resume does not have to re-infer.
        try:
            merged = {
                **launch,
                "genre": genre,
                "chapters": chapters or launch.get("chapters"),
                "wordsPerChapter": wpc or launch.get("wordsPerChapter"),
                "perspective": perspective or launch.get("perspective"),
                "proseMode": prose_mode or launch.get("proseMode"),
                "savedAt": datetime.now(timezone.utc).isoformat(),
            }
            paths.save_json_atomic(merged, p / "launch.json")
        except OSError:
            pass

    try:
        meta = run_manager.launch(p, cli)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "project": name, **run_snapshot(p), "logPath": meta.get("logPath")}


@app.get("/api/run-status")
def run_status(project: str | None = Query(None)):
    _, p = project_dir(project)
    return {"project": p.name, **run_snapshot(p)}


# --------------------------------------------------------------- snapshots


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


@app.get("/api/run-state")
def run_state(project: str | None = Query(None)):
    _, p = project_dir(project)
    return {
        "startedAt": _iso((p / "state.json").stat().st_mtime),
        **run_state_fields(p, load_state(p)),
    }


@app.get("/api/score-history")
def score_history(project: str | None = Query(None)):
    _, p = project_dir(project)
    results = p / "results.tsv"
    out = []
    if results.exists():
        for i, line in enumerate(results.read_text(encoding="utf-8").splitlines()[1:], 1):
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            _commit, phase, score, _words, status, _desc = parts[:6]
            out.append({
                "iteration": i,
                "score": float(score),
                "kept": status == "keep",
                "phase": phase,
            })
    return out


@app.get("/api/llm-events")
def llm_events(project: str | None = Query(None)):
    _, p = project_dir(project)
    events = []
    f = p / "llm_events.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            events.append({
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
            })
    return events


@app.get("/api/foundation")
def foundation(project: str | None = Query(None)):
    _, p = project_dir(project)
    return gen.gen_foundation(p, load_state(p))


@app.get("/api/ledger")
def ledger(project: str | None = Query(None)):
    _, p = project_dir(project)
    return gen.gen_ledger(p, load_state(p))


@app.get("/api/chapters")
def chapters(project: str | None = Query(None)):
    _, p = project_dir(project)
    return gen.gen_chapters(p, load_state(p))


@app.get("/api/evals")
def evals(project: str | None = Query(None)):
    _, p = project_dir(project)
    return gen.gen_evals(p)


@app.get("/api/revision")
def revision(project: str | None = Query(None)):
    _, p = project_dir(project)
    return gen.gen_revision(p)


@app.get("/api/tournament")
def tournament(project: str | None = Query(None)):
    _, p = project_dir(project)
    return gen.gen_tournament(p)



