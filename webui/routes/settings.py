"""Settings endpoints — read/patch the repo .env."""

import sys
from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parent.parent.parent
WEBUI_DIR = _Path(__file__).resolve().parent.parent
for _p in (ROOT, WEBUI_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi import APIRouter, HTTPException, Query, Request  # noqa: E402
from starlette.responses import StreamingResponse  # noqa: E402

from deps import (  # noqa: E402
    load_state, norm_phase, project_dir, run_manager, run_state_fields,
)

import os
from pathlib import Path

from core import paths
from pipeline import pipeline_infra

from pydantic import BaseModel, Field

router = APIRouter()


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


@router.get("/api/settings")
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


@router.post("/api/settings")
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
