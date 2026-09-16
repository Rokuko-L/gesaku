"""SSE stream: state snapshots + log/llm tails."""

import sys
from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parent.parent.parent
WEBUI_DIR = _Path(__file__).resolve().parent.parent
for _p in (ROOT, WEBUI_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi import APIRouter, Query, Request  # noqa: E402
from starlette.responses import StreamingResponse  # noqa: E402

from deps import (  # noqa: E402
    llm_event_view, load_state, project_dir, run_manager, run_state_fields,
)

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

router = APIRouter()


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


# The pipeline's own line shapes (core/pipeline `step()` and `banner()`), used
# to give each log line a real level. The bridge used to tag everything "raw",
# which the UI rendered as "[llm]" — so run output, banners and tracebacks were
# all labelled as model calls.
_SEPARATOR_RE = re.compile(r"^\s*[=\-]{5,}\s*$")
_STEP_RE = re.compile(r"^\s*\[\d{2}:\d{2}:\d{2}\]")


def _line_level(text: str, prev_was_separator: bool = False) -> str:
    """Classify one pipeline stdout line: banner | step | warn | raw.

    `banner()` prints a separator, the title, then a separator again, so a line
    that directly follows a separator is a banner title.
    """
    stripped = text.strip()
    if _SEPARATOR_RE.match(stripped):
        return "banner"
    lowered = stripped.lower()
    if any(tok in lowered for tok in
           ("error", "fatal", "traceback", "warning", "warn:", "failed",
            "exception")):
        return "warn"
    if _STEP_RE.match(stripped):
        return "step"
    # banner() prints sep / title / sep, so the line after a separator is a
    # title — unless it is structured output ("k=v") that merely happens to sit
    # under a closing rule.
    if prev_was_separator and stripped and "=" not in stripped:
        return "banner"
    return "raw"


def _tail_seed(path: Path, max_bytes: int = 65536) -> int:
    """Byte offset ~max_bytes before EOF, snapped forward to a line boundary.

    Used when a stream opens on an existing log. Starting at EOF leaves the pane
    empty for a *finished* run — nothing more will ever be written — which is
    exactly the run an operator wants to read afterwards. Backfill a tail
    instead; for a live run this also gives immediate context.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if size <= max_bytes:
        return 0
    with open(path, "rb") as fh:
        fh.seek(size - max_bytes)
        fh.readline()  # discard the partial line we landed inside
        return fh.tell()


@router.get("/api/stream")
async def stream(request: Request, project: str | None = Query(None)):
    """SSE feed for one project: `state` snapshots (~2s), `log` tail lines,
    `llm` tail events. Tail sources cover runs launched from the CLI too —
    the pipeline always writes logs/<ts>_pipeline.log."""
    _, p = project_dir(project)

    async def event_gen():
        log_path = run_manager.log_path(p)
        # Backfill the tail rather than starting at EOF — see _tail_seed.
        log_offset = _tail_seed(log_path) if log_path and log_path.exists() else 0
        llm_path = p / "llm_events.jsonl"
        llm_offset = llm_path.stat().st_size if llm_path.exists() else 0
        prev_sep = False  # track banner structure across lines
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
            # Re-resolve every tick: a resumed run writes a NEW timestamped log
            # while the old one still exists, so holding the first path would
            # tail a dead file forever ("waiting for output" with a live run).
            current_log = run_manager.log_path(p)
            if current_log != log_path:
                log_path = current_log
                log_offset = _tail_seed(log_path) if log_path and log_path.exists() else 0
            if log_path is not None:
                log_offset, lines = await asyncio.to_thread(_new_lines, log_path, log_offset)
                for line in lines:
                    frames.append(_sse("log", {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "level": _line_level(line, prev_sep), "text": line,
                    }))
                    prev_sep = bool(_SEPARATOR_RE.match(line.strip()))
            llm_offset, lines = await asyncio.to_thread(_new_lines, llm_path, llm_offset)
            for line in lines:
                if line.strip():
                    try:
                        frames.append(_sse("llm", llm_event_view(json.loads(line))))
                    except json.JSONDecodeError:
                        pass
            if frames:
                yield "".join(frames)
            await asyncio.sleep(1.0)

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
