"""Bounded open-pass continuity judge (tool-using, budget-capped).

CLI: uv run python pipeline/continuity_open.py <chapter_num> [--stability]

Prompt-level exclusion of Findings A is NOT enforced — host records
overlap_a_tool_targets as a post-hoc diagnostic. Gates warn only.
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import re
import time
from pathlib import Path

from core import continuity_text
from core import paths
from core.llm import call_llm_tools, parse_json_response
from core.validation import (
    ContinuityFinding,
    ContinuityVerdict,
    OutputValidationError,
    parse_validated,
)
from pipeline.continuity_closed import run_closed_pass
from pipeline.pipeline_infra import judge_tool_budget
from pydantic import BaseModel, ConfigDict, Field  # noqa: F401  (re-export surface)


def _load(path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def _safe_project_path(p) -> Path:
    root = paths.get_project_dir().resolve()
    resolved = Path(p).resolve()
    # is_relative_to blocks sibling-prefix escapes (…/foo vs …/foo2).
    if not resolved.is_relative_to(root):
        raise ValueError(f"path escapes project: {p}")
    return resolved


class ContinuityTools:
    """Read-only project tools for the open-pass judge."""

    def __init__(self, chapter_num: int):
        self.chapter_num = chapter_num

    def __call__(self, name: str, inp: dict):
        inp = inp or {}
        chapters_dir = paths.get_chapters_dir()
        if name == "search_canon":
            return self._search(_load(paths.get_canon_path()), inp.get("query") or inp.get("q") or "")
        if name == "read_chapter":
            n = int(inp.get("chapter") or inp.get("n") or 0)
            p = _safe_project_path(chapters_dir / f"ch_{n:02d}.md")
            return _load(p)[:20000]
        if name == "search_prior_chapters":
            q = inp.get("query") or inp.get("q") or ""
            hits = []
            for n in range(1, self.chapter_num):
                text = _load(chapters_dir / f"ch_{n:02d}.md")
                if q and re.search(re.escape(q), text, re.IGNORECASE):
                    for line in text.splitlines():
                        if q.lower() in line.lower():
                            hits.append(f"ch{n}: {line.strip()[:200]}")
            return "\n".join(hits[:40]) or f"(no hits for {q!r})"
        if name == "read_outline_chapter":
            n = int(inp.get("chapter") or inp.get("n") or self.chapter_num)
            outline = _load(paths.get_outline_path())
            try:
                from core.outline import extract_chapter_outline
                return extract_chapter_outline(outline, n)
            except (OSError, ValueError) as e:
                print(f"WARN: read_outline_chapter({n}) fallback: {e}", file=sys.stderr)
                return outline[:5000]
        if name == "search_characters":
            return self._search(_load(paths.get_characters_path()), inp.get("query") or inp.get("name") or "")
        if name == "search_world":
            return self._search(_load(paths.get_world_path()), inp.get("query") or inp.get("q") or "")
        return f"ERROR: unknown tool {name!r}"

    @staticmethod
    def _search(text: str, query: str) -> str:
        if not query:
            return text[:4000]
        lines = []
        for line in text.splitlines():
            if query.lower() in line.lower():
                lines.append(line)
        return "\n".join(lines[:40]) or f"(no hits for {query!r})"


TOOL_SPECS = [
    {
        "name": "search_canon",
        "description": "Search canon.md for facts matching a query string.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "read_chapter",
        "description": "Read a chapter file by number (1-based).",
        "input_schema": {
            "type": "object",
            "properties": {"chapter": {"type": "integer"}},
            "required": ["chapter"],
        },
    },
    {
        "name": "search_prior_chapters",
        "description": "Grep chapters before the current one for a query string.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "read_outline_chapter",
        "description": "Read the detailed outline entry for a chapter number.",
        "input_schema": {
            "type": "object",
            "properties": {"chapter": {"type": "integer"}},
            "required": ["chapter"],
        },
    },
    {
        "name": "search_characters",
        "description": "Search characters.md for a name or phrase.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_world",
        "description": "Search world.md for a place or lore phrase.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


def run_open_pass(chapter_num: int, *, budget: int | None = None, stability: bool = False) -> dict:
    closed = run_closed_pass(chapter_num)
    findings_a = closed.get("findings") or []
    chapter_text = _load(paths.get_chapters_dir() / f"ch_{chapter_num:02d}.md")
    try:
        title = paths.get_novel_title()
    except Exception:
        title = "Untitled"

    if findings_a:
        a_lines = "\n".join(
            f"- ({f.get('id')}) [{f.get('kind')}] {f.get('claim')} "
            f"(chapters={f.get('chapters')}, trust={f.get('trust')})"
            for f in findings_a
        )
    else:
        a_lines = "(none)"

    system = paths.load_prompt("continuity_system")
    user = paths.format_prompt(
        paths.load_prompt("continuity_verdict"),
        chapter_num=chapter_num,
        title=title,
        chapter_text=chapter_text[:12000],
        findings_a_block=a_lines,
    )

    if budget is None:
        budget = judge_tool_budget()

    executor = ContinuityTools(chapter_num)
    result = call_llm_tools(
        [{"role": "user", "content": user}],
        TOOL_SPECS,
        executor,
        system=system,
        model_key="judge",
        max_tokens=8000,
        temperature=0.2,
        beta_context=True,
        timeout_role="xlong",
        budget=budget,
    )

    raw = result.text or ""
    verdict_data = {}
    verdict_error = None
    try:
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict):
            verdict_error = f"non-object JSON: {type(parsed).__name__}"
            verdict_data = {
                "findings": [],
                "leads_exhausted": False,
                "notes": f"verdict parse failed: {verdict_error}",
            }
        else:
            validated = parse_validated(
                ContinuityVerdict, json.dumps(parsed), context="Continuity verdict"
            )
            verdict_data = validated.model_dump()
    except (OutputValidationError, ValueError, json.JSONDecodeError) as e:
        verdict_error = str(e)[:300]
        verdict_data = {
            "findings": [],
            "leads_exhausted": False,
            "notes": f"verdict parse failed: {verdict_error}",
        }

    agent_stop = result.agent_stop
    if agent_stop == "end_turn" and verdict_data.get("leads_exhausted"):
        agent_stop = "leads_exhausted"

    findings_b = []
    for f in verdict_data.get("findings") or []:
        if isinstance(f, dict):
            f.setdefault("author_only", False)
            f.setdefault("severity", "warn")
            findings_b.append(f)
        elif hasattr(f, "model_dump"):
            findings_b.append(f.model_dump())

    overlap = continuity_text.overlap_a_tool_targets(findings_a, result.trace)

    out = {
        "chapter": chapter_num,
        "findings_a": findings_a,
        "findings_b": findings_b,
        "merged": findings_a + findings_b,
        "agent_stop": agent_stop,
        "wire_stop_reason": result.stop_reason,
        "tool_calls_used": result.tool_calls_used,
        "budget": result.budget,
        "trace": result.trace,
        "overlap_a_tool_targets": overlap,
        "model": result.model,
        "provider": result.provider,
        "verdict_error": verdict_error,
        "notes": verdict_data.get("notes") or "",
        "trust_note": closed.get("trust_note"),
        "merged_scope": "author-scope audit data — not writer fuel",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if stability:
        out["stability"] = {
            "kind": "single_run_snapshot",
            "overlap_a_tool_targets": overlap,
            "agent_stop": agent_stop,
            "n_findings_b": len(findings_b),
        }

    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    stability = "--stability" in sys.argv
    if not args:
        print("Usage: python pipeline/continuity_open.py <chapter_num> [--stability]", file=sys.stderr)
        return 2
    ch = int(args[0])
    try:
        result = run_open_pass(ch, stability=stability)
    except Exception as e:
        # Always leave an auditable sidecar — never a bare traceback only.
        result = {
            "chapter": ch,
            "findings_a": [],
            "findings_b": [],
            "merged": [],
            "agent_stop": "error",
            "wire_stop_reason": "error",
            "tool_calls_used": 0,
            "budget": judge_tool_budget(),
            "trace": [],
            "overlap_a_tool_targets": 0.0,
            "verdict_error": str(e)[:300],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        out_path = paths.get_eval_logs_dir() / f"continuity_ch{ch:02d}.json"
        paths.save_json_atomic(result, out_path)
        print(f"continuity_open ch{ch:02d}: agent_stop=error ({e}) -> {out_path}", file=sys.stderr)
        return 0
    out_path = paths.get_eval_logs_dir() / f"continuity_ch{ch:02d}.json"
    paths.save_json_atomic(result, out_path)
    print(
        f"continuity_open ch{ch:02d}: stop={result['agent_stop']} "
        f"tools={result['tool_calls_used']}/{result['budget']} "
        f"overlap={result['overlap_a_tool_targets']} "
        f"B={len(result['findings_b'])} -> {out_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
