"""Parseable run progress.

One owner for the machine-readable event line, so `foundation/`, `pipeline/`
and any future caller emit the same format. `foundation/` cannot import
`pipeline/` (shared helpers belong in `core/`), which is why this lives here
rather than in pipeline_infra.

Format: a single line prefixed `#GESAKU:` followed by a JSON object. A
consumer finds every event with one grep and never has to parse prose:

    #GESAKU: {"ts":"2026-09-27T23:01:35","event":"stage_start","stage":"gen_outline"}

`event` is one of:

    stage_start / stage_done / stage_error  a bracketed unit of work, with
                                           `elapsed_s` on the way out
    step                                    a named step within a stage
    retry                                   an attempt N of M
    note                                    free-form detail worth surfacing
    progress                                a live measurement (chapters done,
                                           score, call count)

Emission never raises: a broken progress line must not kill a run.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

PREFIX = "#GESAKU:"


def emit(event: str, **fields) -> None:
    """Write one parseable progress event to stdout.

    Fail-soft by design. Progress is observability, not control flow, so a
    closed stdout or an unserialisable value must not abort the caller.
    """
    try:
        payload = {"ts": datetime.now().isoformat(timespec="seconds"),
                   "event": event}
        for key, value in fields.items():
            if value is not None:
                payload[key] = value
        print(f"{PREFIX} {json.dumps(payload, default=str)}", flush=True)
    except Exception:
        pass


def parse_line(line: str) -> dict | None:
    """Parse a `#GESAKU:` line, or None if it is not one.

    Used by the webui SSE feed and by tests; keeps the format defined in one
    place instead of by every consumer's regex.
    """
    line = (line or "").strip()
    if not line.startswith(PREFIX):
        return None
    try:
        obj = json.loads(line[len(PREFIX):].strip())
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) and obj.get("event") else None


class stage_timer:
    """Bracket a stage with `stage_start` / `stage_done` (or `stage_error`).

    The elapsed time on the way out is the number that actually shows where a
    run spent its hours, and the start event is what makes "stuck" visible
    while the stage is still running.
    """

    def __init__(self, stage: str, **fields):
        self.stage = stage
        self.fields = fields
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.monotonic()
        emit("stage_start", stage=self.stage, **self.fields)
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = round(time.monotonic() - self.t0, 1)
        if exc_type is not None:
            emit("stage_error", stage=self.stage, elapsed_s=elapsed,
                 detail=str(exc)[:200] if exc else exc_type.__name__,
                 **self.fields)
            return False
        emit("stage_done", stage=self.stage, elapsed_s=elapsed, **self.fields)
        return False
