"""Shared phase helpers: canon sync + post-keep hooks.

Used by both the drafting and revision phases, so it lives below both.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import json
import re
import sys
from pathlib import Path

from core import paths
from core import canon as canon_mod
from core.outline import extract_outline_debts

from pipeline.pipeline_infra import (
    chapter_threshold, near_clean_margin, run_tool, step, timeout_for, uv_run,
)



def update_canon_from_eval(ch: int, attempt_num: int = None, eval_log_path=None):
    """
    Append new canon entries and unexplained references from the evaluation log of chapter ch.
    If eval_log_path is provided (parsed from the evaluate.py stdout line 'eval_log: <path>'),
    that exact log is used — positional indexing into the log directory is unreliable because
    the directory also accumulates logs from other phases and discarded attempts.
    Otherwise fall back to the most recent log.
    """
    try:
        if eval_log_path is not None and Path(eval_log_path).exists():
            eval_path = Path(eval_log_path)
        else:
            eval_log_pattern = f"*_ch{ch:02d}.json"
            eval_logs = sorted(paths.get_eval_logs_dir().glob(eval_log_pattern))
            if not eval_logs:
                return
            if attempt_num is not None and attempt_num <= len(eval_logs):
                eval_path = eval_logs[attempt_num - 1]
            else:
                eval_path = eval_logs[-1]

        eval_data = json.loads(eval_path.read_text(encoding="utf-8"))
        raw_entries = eval_data.get("new_canon_entries", [])
        unexplained = eval_data.get("unexplained_references", [])

        # Normalize entries: support both legacy str list and new {fact, scope} format
        core_entries = []
        inc_entries = []
        for entry in raw_entries:
            if isinstance(entry, str):
                inc_entries.append(entry)
            elif isinstance(entry, dict):
                fact = entry.get("fact", "")
                scope = entry.get("scope", "incremental")
                (core_entries if scope == "core" else inc_entries).append(fact)

        if core_entries or inc_entries or unexplained:
            canon_path = paths.get_canon_path()
            canon_text = canon_path.read_text(encoding="utf-8") if canon_path.exists() else ""
            with canon_path.open("a", encoding="utf-8") as f:
                if core_entries:
                    if "## Core Canon" not in canon_text:
                        f.write(f"\n\n## Core Canon\n\n")
                        canon_text += "\n\n## Core Canon\n\n"
                    for entry in core_entries:
                        f.write(f"- {entry}\n")
                if inc_entries:
                    ch_header = f"## As of Chapter {ch}"
                    if ch_header not in canon_text:
                        f.write(f"\n\n{ch_header}\n\n")
                    else:
                        f.write(f"\n")
                    for entry in inc_entries:
                        f.write(f"- {entry}\n")
                if unexplained:
                    f.write("\n**Unexplained references:**\n")
                    for ref in unexplained:
                        f.write(f"- {ref}\n")
    except (json.JSONDecodeError, KeyError, OSError) as e:
        print(f"  WARN: Could not extract canon entries from eval log: {e}", file=sys.stderr)


def on_chapter_kept(ch: int, reextract: bool = False) -> None:
    """Fail-soft post-keep hook: extract prose-emergent micro-plants.

    Never blocks drafting/revision. Distinct from outline-tag `state["debts"]`.
    """
    script = "pipeline/extract_micro_plants.py"
    cmd = f'"{sys.executable}" {script} {ch}'
    if reextract:
        cmd += " --reextract"
    try:
        rep = run_tool(cmd, timeout=timeout_for("short"), check=False)
        if rep.returncode != 0:
            step(f"micro-plant extract skipped for ch{ch} (rc={rep.returncode})")
    except Exception as e:
        step(f"micro-plant extract failed for ch{ch}: {e}")


CALLBACKS_SIDECAR = "open_callbacks.json"


def stage_chapter_with_callbacks(ch: int) -> None:
    """Stage a chapter together with the plant store extracted from it.

    Only safe on a KEEP path — one that immediately follows with
    `git_commit_staged`, which commits the whole index. `git add` here is
    therefore a promise that the very next commit will carry both files.

    Do NOT call this on a revert path. There is no commit there, so the
    staged store would sit in the index; a later `git reset --hard` would
    discard it and restore a stale copy, and a later chapter's
    `git_commit_staged` would sweep it in under the wrong message. Reverts
    re-extract into the working tree only and let the next cycle-end
    `git_add_commit` pick the store up.
    """
    project = str(paths.get_project_dir())
    rel_paths = [f"chapters/ch_{ch:02d}.md"]
    if paths.get_open_callbacks_path().exists():
        rel_paths.append(CALLBACKS_SIDECAR)
    run_tool(f"git add {' '.join(rel_paths)}", cwd=project)


REVISION_CANON_HEADER = "## Revision Sync"


def resync_canon_after_cycle(kept_results: list, cycle: int):
    """Rebuild the ## Revision Sync section from this cycle's kept chapter evals.

    Foundation / draft-era ## As of Chapter N sections are left intact. The
    revision-sync block is replaceable so later cycles do not stack duplicates.
    """
    core_entries: list[str] = []
    inc_entries: list[str] = []
    for r in kept_results:
        log_path = r.get("eval_log_path")
        if not log_path:
            continue
        p = Path(log_path)
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARN: canon resync could not read {p}: {e}", file=sys.stderr)
            continue
        for entry in data.get("new_canon_entries") or []:
            if isinstance(entry, str):
                inc_entries.append(entry)
            elif isinstance(entry, dict):
                fact = entry.get("fact", "")
                if not fact:
                    continue
                (core_entries if entry.get("scope") == "core" else inc_entries).append(fact)

    # Dedupe while preserving order
    def _uniq(items):
        seen = set()
        out = []
        for x in items:
            key = x.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(x.strip())
        return out

    core_entries = _uniq(core_entries)
    inc_entries = _uniq(inc_entries)
    if not core_entries and not inc_entries:
        return

    canon_path = paths.get_canon_path()
    text = canon_path.read_text(encoding="utf-8") if canon_path.exists() else ""
    text = re.sub(
        rf"\n*{re.escape(REVISION_CANON_HEADER)}\n.*?(?=\n## |\Z)",
        "\n",
        text,
        flags=re.DOTALL,
    )
    block = [f"\n{REVISION_CANON_HEADER}\n\n", f"*Cycle {cycle} post-revision extraction*\n"]
    if core_entries:
        block.append("\n### Core\n\n")
        block.extend(f"- {e}\n" for e in core_entries)
    if inc_entries:
        block.append("\n### Incremental\n\n")
        block.extend(f"- {e}\n" for e in inc_entries)
    canon_path.write_text(text.rstrip() + "\n" + "".join(block), encoding="utf-8")
    step(f"Canon resync: {len(core_entries)} core, {len(inc_entries)} incremental "
         f"entries from {len(kept_results)} revised chapter(s)")


DEFAULT_NEAR_CLEAN_MESSAGE = (
    "YOUR PREVIOUS DRAFT WAS EVALUATED AS CLEAN"
)


def build_eval_feedback(eval_log_path):
    """Build targeted retry feedback from a failed chapter eval JSON.

    Extracts the judge's AI-pattern findings, top-3 revisions, and the
    mechanical slop tic report so the next draft attempt can address them.
    Returns (feedback_string, near_clean_flag).
    near_clean is True when the draft missed the keep bar by a hair with
    negligible mechanical penalties — in that case the draft should be KEPT
    rather than retried, because a blind regeneration from a deleted draft
    regresses (observed: ch20 6.4 -> 3.32, 4.5, 3.24; ch13 6.25 -> 4.22).
    """
    try:
        if not eval_log_path or not Path(eval_log_path).exists():
            return "", False
        data = json.loads(Path(eval_log_path).read_text(encoding="utf-8"))
    except Exception:
        return "", False

    lines = []

    ai_patterns = data.get("ai_patterns_detected") or []
    if ai_patterns:
        lines.append("AI PATTERNS THE JUDGE DETECTED IN YOUR PREVIOUS DRAFT (eliminate these):")
        for p in ai_patterns:
            lines.append(f"  - {p}")

    revisions = data.get("top_3_revisions") or []
    if revisions:
        lines.append("JUDGE'S PRIORITY REVISIONS:")
        for r in revisions:
            lines.append(f"  - {r}")

    slop = data.get("slop") or {}
    tics = slop.get("prose_tics") or []
    if tics:
        lines.append("MECHANICAL TIC REPORT (density per 3000 words — rewrite these constructions "
                     "with varied syntax; one use is fine, clusters are not):")
        for t in tics:
            lines.append(f"  - {t['tic']}: {t['count']}x ({t['per_3k']} per 3k words)")

    struct = slop.get("structural_ai_tics") or []
    if struct:
        lines.append("STRUCTURAL FORMULAIC PATTERNS:")
        for name, cnt in struct:
            lines.append(f"  - {name} ({cnt}x)")

    length_penalty = data.get("length_penalty") or 0.0
    if length_penalty > 0.5:
        lines.append(
            f"LENGTH: your draft was over the chapter's word budget (length penalty "
            f"-{length_penalty:.1f}). Rewrite it tighter: hit the target word count, "
            "finish every outline beat, and end decisively. Compression beats expansion."
        )

    # Near-clean detection: the draft missed the keep bar by a hair with
    # negligible mechanical penalties (raw judge score high, tic/slop
    # penalties tiny). In this state a blind retry (draft deleted, fresh
    # generation) regresses — keep the draft instead (observed: ch20 6.4 ->
    # 3.32, 4.5, 3.24; ch13 6.25 -> 4.22).
    try:
        raw_score = float(data.get("raw_judge_score") or 0)
        adjusted_score = float(data.get("overall_score") or 0)
    except (TypeError, ValueError):
        raw_score = adjusted_score = 0.0
    slop_penalty = slop.get("slop_penalty") or 0.0
    tic_penalty = slop.get("prose_tic_penalty") or 0.0
    near_clean = (raw_score >= chapter_threshold()
                  and adjusted_score >= chapter_threshold() - near_clean_margin()
                  and slop_penalty < 2.0 and tic_penalty < 1.0)

    if not lines:
        # The judge found nothing wrong. A clean eval with empty feedback
        # makes the model rewrite the chapter wholesale and re-introduce tics.
        return ("YOUR PREVIOUS DRAFT WAS EVALUATED AS CLEAN — no AI patterns, no tic clusters, "
                "and no structural issues detected.\n"
                "DO NOT rewrite the chapter wholesale. Keep the accepted prose essentially as-is. "
                "Only make surgical changes if a specific problem exists (e.g. unresolved canon). "
                "If nothing needs changing, produce the same text verbatim."), near_clean

    if near_clean:
        lines.append("NOTE: your raw judge score was strong and the mechanical detectors added "
                     "almost no penalty — this draft missed the keep bar by a hair. Do NOT rewrite "
                     "it wholesale. Address each listed point with surgical edits and preserve "
                     "everything the judge did not flag. If the points above are already satisfied, "
                     "produce the same text verbatim.")
    return "\n".join(lines), near_clean
