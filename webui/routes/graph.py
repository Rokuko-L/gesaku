"""Entity-graph endpoints (cached LLM arrangement + fallback)."""

import sys
from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parent.parent.parent
WEBUI_DIR = _Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "scratch", WEBUI_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi import APIRouter, HTTPException, Query, Request  # noqa: E402
from starlette.responses import StreamingResponse  # noqa: E402

from deps import (  # noqa: E402
    load_state, norm_phase, project_dir, run_manager, run_state_fields,
)

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import gen_webui_fixtures as gen
from core import paths
from pydantic import BaseModel, Field

router = APIRouter()


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


@router.get("/api/entity-graph")
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


@router.post("/api/entity-graph")
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
