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
for _p in (ROOT, ROOT / "scratch", WEBUI_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core import paths  # noqa: E402
import gen_webui_fixtures as gen  # noqa: E402
from pipeline import pipeline_infra  # noqa: E402
from run_manager import RunManager, SEEDS_DIR  # noqa: E402

run_manager = RunManager()

app = FastAPI(title="gesaku operator console", docs_url="/api/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_proj_lock = threading.Lock()


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


# ------------------------------------------------------------ entity graph

GRAPH_CACHE = ".entity_graph.json"


class GraphNode(BaseModel):
    name: str
    group: str
    importance: int = Field(ge=1, le=10)
    summary: str = ""


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: str = "unknown"
    label: str = ""


class GraphArrangement(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


GRAPH_SYSTEM = paths.load_prompt("entity_graph_system")

GRAPH_PROMPT = paths.load_prompt("entity_graph")


def _excerpt(text: str, cap: int) -> str:
    """Head+tail excerpt when a doc exceeds the prompt budget.

    Canon entries are appended while drafting, so the tail holds the newest
    facts and the head the foundation-era ones; the middle is what gets cut.
    """
    if len(text) <= cap:
        return text
    head = cap // 3
    tail = cap - head
    marker = "\n…[middle of document truncated to fit the prompt budget]…\n"
    return text[:head] + marker + text[-tail:]


def _graph_input_fingerprint(p: Path) -> dict:
    """Size+mtime of the source docs — used to flag a stale cached arrangement."""
    out = {}
    for name in ("characters.md", "world.md", "canon.md"):
        f = p / name
        if f.exists():
            st = f.stat()
            out[name] = [st.st_size, int(st.st_mtime)]
    return out


def _heuristic_entities(p: Path, state: dict) -> dict:
    """Co-mention graph from the fixture generator — the no-LLM fallback."""
    return gen.gen_foundation(p, state)["entities"]


def _graph_to_entities(arr: GraphArrangement) -> dict:
    """Map the LLM arrangement into the contract's entities shape."""
    nodes = [
        {
            "id": f"n{i}", "label": n.name.strip(), "kind": "character",
            "group": n.group, "importance": n.importance,
            "desc": n.summary, "mentions": [], "status": None,
        }
        for i, n in enumerate(arr.nodes)
    ]
    name_to_id = {n["label"].lower(): n["id"] for n in nodes}
    edges = []
    for e in arr.edges:
        src, dst = name_to_id.get(e.source.strip().lower()), name_to_id.get(e.target.strip().lower())
        if src and dst and src != dst:
            edges.append({"from": src, "to": dst, "label": e.label, "kind": e.kind})
    return {"nodes": nodes, "edges": edges}


@app.get("/api/entity-graph")
def entity_graph(project: str | None = Query(None)):
    """Cached LLM arrangement if present, else the heuristic co-mention graph.
    `stale` flags a cache whose source docs changed since it was generated."""
    _, p = project_dir(project)
    cache = p / GRAPH_CACHE
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            cached["llm"] = True
            if cached.get("inputs") is not None:
                cached["stale"] = cached["inputs"] != _graph_input_fingerprint(p)
            return cached
        except json.JSONDecodeError:
            pass  # corrupt cache — fall through to the heuristic graph
    return {"llm": False, "entities": _heuristic_entities(p, load_state(p))}


@app.post("/api/entity-graph")
def arrange_entity_graph(project: str | None = Query(None)):
    """Ask the writer model to arrange the entity graph; cache the result."""
    _, p = project_dir(project)
    state = load_state(p)
    chars = (p / "characters.md")
    world = (p / "world.md")
    if not chars.exists():
        raise HTTPException(404, "no characters.md on disk — the foundation phase hasn't run")
    canon = (p / "canon.md")

    prompt = GRAPH_PROMPT.format(
        characters=_excerpt(chars.read_text(encoding="utf-8"), 24000),
        world=_excerpt(world.read_text(encoding="utf-8"), 20000) if world.exists() else "(none)",
        canon=_excerpt(canon.read_text(encoding="utf-8"), 50000) if canon.exists() else "(none yet)",
    )
    from core.llm import call_llm
    from core.validation import OutputValidationError, parse_validated

    text = call_llm(prompt, system=GRAPH_SYSTEM, model_key="writer",
                    max_tokens=8000, temperature=0.2)
    try:
        arr = parse_validated(GraphArrangement, text, context="entity graph")
    except OutputValidationError as e:
        # one self-correction retry with the validator's feedback
        text = call_llm(
            f"{prompt}\n\nYour previous answer was rejected: {e.feedback}\n"
            "Answer again with corrected JSON only.",
            system=GRAPH_SYSTEM, model_key="writer", max_tokens=8000, temperature=0.1)
        try:
            arr = parse_validated(GraphArrangement, text, context="entity graph retry")
        except OutputValidationError as e2:
            raise HTTPException(502, f"llm graph arrangement failed validation: {e2.feedback}") from e2

    entities = _graph_to_entities(arr)
    if not entities["nodes"]:
        raise HTTPException(502, "llm returned an empty graph")
    cache = {"generatedAt": datetime.now(timezone.utc).isoformat(),
             "inputs": _graph_input_fingerprint(p),
             "entities": entities}
    tmp = p / (GRAPH_CACHE + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p / GRAPH_CACHE)
    return {"llm": True, "generatedAt": cache["generatedAt"], "entities": entities}


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


def _mask(key: str | None) -> str:
    if not key:
        return ""
    return f"{key[:7]}…{key[-4:]}" if len(key) > 14 else "…"


def _env_path() -> Path:
    return ROOT / ".env"


def _read_env_file() -> dict[str, str]:
    path = _env_path()
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        data[k.strip()] = v.strip()
    return data


def _write_env_file(updates: dict[str, str]) -> None:
    """Merge keys into .env. Comments and unrelated keys are preserved."""
    path = _env_path()
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = {k: v for k, v in updates.items()}
    out: list[str] = []
    seen: set[str] = set()
    for line in existing:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, _ = s.partition("=")
            k = k.strip()
            if k in remaining:
                val = remaining.pop(k)
                out.append(f"{k}={val}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in remaining.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    # Make subsequent os.getenv / load_dotenv-less readers see new values
    for k, v in updates.items():
        os.environ[k] = v


class SettingsPayload(BaseModel):
    baseUrl: str | None = None
    apiKey: str | None = None  # full key; omit or leave masked to keep existing
    models: dict[str, str] | None = None
    thresholds: dict[str, float] | None = None
    heuristics: dict[str, float] | None = None
    defaults: dict[str, str | int] | None = None


@app.get("/api/settings")
def settings():
    env = _read_env_file()
    # Merge process env over file (load_dotenv already applied at import)
    merged = {**env, **{k: v for k, v in os.environ.items()}}
    genre = merged.get("GESAKU_GENRE", "")
    try:
        chapter_count = int(float(merged.get("GESAKU_CHAPTERS", "24") or 24))
    except ValueError:
        chapter_count = 24
    return {
        "baseUrl": merged.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        "apiKeyMasked": _mask(merged.get("ANTHROPIC_API_KEY")),
        "models": {r: merged.get(f"GESAKU_{r.upper()}_MODEL", "writer_combo")
                   for r in ("writer", "judge", "review")},
        "thresholds": {
            "foundation": pipeline_infra.foundation_threshold(),
            "chapter": pipeline_infra.chapter_threshold(),
        },
        "heuristics": {
            "maxChapterAttempts": pipeline_infra.max_chapter_attempts(),
            "revisionCycles": pipeline_infra.min_revision_cycles(),
            "plateauDelta": pipeline_infra.plateau_delta(),
        },
        "defaults": {
            "genre": genre,
            "chapterCount": chapter_count,
            "notes": merged.get("GESAKU_NOTES", ""),
        },
    }


@app.post("/api/settings")
def settings_commit(payload: SettingsPayload):
    updates: dict[str, str] = {}
    if payload.baseUrl:
        updates["ANTHROPIC_BASE_URL"] = payload.baseUrl.strip()
    if payload.apiKey and "…" not in payload.apiKey and len(payload.apiKey.strip()) > 8:
        updates["ANTHROPIC_API_KEY"] = payload.apiKey.strip()
    if payload.models:
        for role in ("writer", "judge", "review"):
            val = (payload.models.get(role) or "").strip()
            if val:
                updates[f"GESAKU_{role.upper()}_MODEL"] = val
    if payload.thresholds:
        if payload.thresholds.get("foundation") is not None:
            updates["GESAKU_FOUNDATION_THRESHOLD"] = str(float(payload.thresholds["foundation"]))
        if payload.thresholds.get("chapter") is not None:
            updates["GESAKU_CHAPTER_THRESHOLD"] = str(float(payload.thresholds["chapter"]))
    if payload.heuristics:
        if payload.heuristics.get("maxChapterAttempts") is not None:
            updates["GESAKU_MAX_CHAPTER_ATTEMPTS"] = str(int(payload.heuristics["maxChapterAttempts"]))
        if payload.heuristics.get("revisionCycles") is not None:
            updates["GESAKU_MIN_REVISION_CYCLES"] = str(int(payload.heuristics["revisionCycles"]))
        if payload.heuristics.get("plateauDelta") is not None:
            updates["GESAKU_PLATEAU_DELTA"] = str(float(payload.heuristics["plateauDelta"]))
    if payload.defaults:
        genre = payload.defaults.get("genre")
        if genre is not None and str(genre).strip():
            updates["GESAKU_GENRE"] = str(genre).strip()
        if payload.defaults.get("chapterCount") is not None:
            updates["GESAKU_CHAPTERS"] = str(int(payload.defaults["chapterCount"]))
        notes = payload.defaults.get("notes")
        if notes is not None:
            updates["GESAKU_NOTES"] = str(notes)

    if not updates:
        raise HTTPException(400, "no settings to write")

    try:
        _write_env_file(updates)
    except OSError as e:
        raise HTTPException(500, f"failed to write .env: {e}") from e
    return settings()


# ------------------------------------------------------------------ stream


def _new_lines(path: Path, offset: int) -> tuple[int, list[str]]:
    """Read new complete lines past a byte offset; returns (new_offset, lines).

    Byte-based so multi-byte UTF-8 characters can't corrupt the offset.
    Holds a trailing partial line back until it completes; restarts from zero
    if the file shrank (rotated log).
    """
    try:
        size = path.stat().st_size
    except OSError:
        return offset, []
    if size < offset:
        offset = 0
    if size == offset:
        return offset, []
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    if not chunk:
        return offset, []
    if not chunk.endswith(b"\n"):
        cut = chunk.rfind(b"\n")
        if cut == -1:
            return offset, []
        chunk = chunk[:cut + 1]
    return offset + len(chunk), chunk.decode("utf-8", errors="replace").splitlines()


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/stream")
async def stream(request: Request, project: str | None = Query(None)):
    """SSE feed for one project: `state` snapshots (~2s), `log` tail lines,
    `llm` tail events. Tail sources cover runs launched from the CLI too —
    the pipeline always writes logs/<ts>_pipeline.log."""
    _, p = project_dir(project)

    async def event_gen():
        log_path = run_manager.log_path(p)
        log_offset = log_path.stat().st_size if log_path and log_path.exists() else 0
        llm_path = p / "llm_events.jsonl"
        llm_offset = llm_path.stat().st_size if llm_path.exists() else 0
        tick = 0
        while True:
            if await request.is_disconnected():
                return
            tick += 1
            frames = []
            if tick % 2 == 1:
                try:
                    state = load_state(p)
                    frames.append(_sse("state", run_state_fields(p, state)))
                except (json.JSONDecodeError, OSError):
                    pass  # mid-write state.json — skip this tick
            if log_path is None or not log_path.exists():
                log_path = run_manager.log_path(p)
                if log_path and log_path.exists():
                    log_offset = log_path.stat().st_size
            if log_path is not None:
                log_offset, lines = await asyncio.to_thread(_new_lines, log_path, log_offset)
                for line in lines:
                    frames.append(_sse("log", {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "level": "raw", "text": line,
                    }))
            llm_offset, lines = await asyncio.to_thread(_new_lines, llm_path, llm_offset)
            for line in lines:
                if line.strip():
                    try:
                        frames.append(_sse("llm", json.loads(line)))
                    except json.JSONDecodeError:
                        pass
            if frames:
                yield "".join(frames)
            await asyncio.sleep(1.0)

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
