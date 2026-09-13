#!/usr/bin/env python3
"""
run_pipeline.py — Fully automated novel pipeline orchestrator.

Runs the complete gesaku pipeline from seed concept to finished novel.
Manages state, git commits, evaluation, and retry logic.

Usage:
  python run_pipeline.py --project mynovel  # run from current state for project
  python run_pipeline.py --project mynovel --from-scratch     # start fresh
  python run_pipeline.py --project mynovel --from-scratch --genre "Horror" --chapters 8 --notes "haunted school"
                                           # auto-creates seed.txt from notes
  python run_pipeline.py --project mynovel --phase foundation # run only foundation
  python run_pipeline.py --project mynovel --phase drafting   # run only drafting
  python run_pipeline.py --project mynovel --phase revision   # run only revision
  python run_pipeline.py --project mynovel --phase export     # run only export
  python run_pipeline.py --project mynovel --max-cycles 4     # limit revision cycles
"""

from core.llm import call_llm
from core.outline import validate_premise_beats, validate_plants_harvests, extract_outline_debts
from core import novel_tex as novel_tex_module
from core import paths
from core import _utf8
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from core.genre import load_genre
from pipeline.review import should_stop as review_should_stop

load_dotenv()

from pipeline.pipeline_infra import *  # noqa: F401,F403
from pipeline.pipeline_infra import _chapter_num_key  # noqa: F401




# ---------------------------------------------------------------------------
# Constants  (all path-dependent values are resolved at runtime via paths)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Git & registry helpers (Option B: per-project repos)
# ---------------------------------------------------------------------------









# ---------------------------------------------------------------------------
# Helpers: state management
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# Helpers: logging
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# Helpers: subprocess execution
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# Helpers: git operations
# ---------------------------------------------------------------------------













# ---------------------------------------------------------------------------
# Helpers: score parsing
# ---------------------------------------------------------------------------













# ---------------------------------------------------------------------------
# Dynamic seed processor
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# PHASE 1 — FOUNDATION
# ---------------------------------------------------------------------------

def run_foundation(state: dict) -> dict:
    """
    Build planning documents (world, characters, outline, voice, canon).
    Loop until foundation_score > threshold or max iterations reached.
    """
    banner("PHASE 1: FOUNDATION", "=")

    best_score = state.get("foundation_score", 0.0)
    iteration = state.get("iteration", 0)
    threshold = foundation_threshold()
    stall_count = state.get("foundation_stall_count", 0)

    if iteration == 0:
        git_add_commit("initial project setup (seed, config)")

    for i in range(iteration + 1, MAX_FOUNDATION_ITERS + 1):
        banner(f"Foundation Iteration {i}", "-")
        state["iteration"] = i

        # 1. Generate planning documents
        # Thinking models on a local proxy routinely exceed 600s for world/
        # character bibles and outline blocks; the old 600s cap killed
        # gen_world / gen_outline mid-call.
        FOUNDATION_STEP_TIMEOUT = int(os.getenv("GESAKU_FOUNDATION_TIMEOUT", "3600"))
        step("Generating world bible...")
        uv_run("foundation/gen_world.py", timeout=FOUNDATION_STEP_TIMEOUT)

        step("Generating characters...")
        uv_run("foundation/gen_characters.py", timeout=FOUNDATION_STEP_TIMEOUT)

        step("Generating title tournament...")
        current_title = load_state().get("title", "")
        if not current_title or current_title == "Untitled":
            uv_run("foundation/gen_title.py", timeout=FOUNDATION_STEP_TIMEOUT)

        # Canon before outline so plant hygiene can use sealed-fact denylist.
        step("Generating canon...")
        uv_run("foundation/gen_canon.py", timeout=FOUNDATION_STEP_TIMEOUT)

        step("Generating outline (part 1)...")
        # Block-level LLM calls for a 24-chapter thinking model easily exceed
        # 30 minutes when the proxy retries/timeouts stack.
        uv_run("foundation/gen_outline.py", timeout=max(3600, FOUNDATION_STEP_TIMEOUT))

        # Validate Chapter 1 premise beats (pre-draft gate)
        outline_path = paths.get_outline_path()
        genre_cfg = load_genre()
        required_beats = genre_cfg.get("framework", {}).get("premise_arc_beats", [])
        premise_passed = False
        premise_last_error = ""
        for oa in range(1, MAX_OUTLINE_ATTEMPTS + 1):
            outline_text = outline_path.read_text(encoding="utf-8")
            passed, error = validate_premise_beats(required_beats, outline_text)
            if passed:
                premise_passed = True
                step(f"Chapter 1 premise beats validated (attempt {oa})")
                break
            premise_last_error = error
            step(f"Chapter 1 premise beat validation FAILED (attempt {oa}/{MAX_OUTLINE_ATTEMPTS}): {error}")
            if oa == MAX_OUTLINE_ATTEMPTS:
                step("REPEATED FAILURE — check genre framework premise_arc_beats, not just regen")
                break
            step("Regenerating outline with targeted retry feedback...")
            format_hint = (
                " Use bullet format: '- beat_label: scene description'"
                " — one line per beat inside the PREMISE BEATS section."
            )
            uv_run(f'foundation/gen_outline.py --retry-feedback "{error}.{format_hint}"', timeout=900)

        # Write premise validation sidecar
        prem_val_path = paths.get_project_dir() / "premise_validation.json"
        prem_val_path.write_text(json.dumps({
            "passed": premise_passed,
            "attempts": oa if premise_passed else MAX_OUTLINE_ATTEMPTS,
            "last_error": "" if premise_passed else premise_last_error,
        }, indent=2), encoding="utf-8")

        step("Generating outline (part 2 — foreshadowing)...")
        # Each block is one 600s LLM call with up to 3 validation retries; the
        # subprocess cap must cover ALL blocks or a legitimate run gets killed
        # mid-polish, leaving outline.md with only the polished prefix.
        n_blocks = max(1, -(-get_total_chapters(state) // 10))
        uv_run("foundation/gen_outline_part2.py", timeout=max(900, n_blocks * 900))

        step("Sanitizing chapter titles...")
        uv_run("pipeline/sanitize_outline_titles.py", timeout=300)

        # Validate plants & harvests consistency and extract active debts
        outline_text = outline_path.read_text(encoding="utf-8")
        ph_passed, ph_error = validate_plants_harvests(outline_text)
        if not ph_passed:
            step(f"WARNING: Outline plants/harvests validation issues found:\n{ph_error}")

        # Plant hygiene: sealed-term leaks + action-plant coverage (twist stories)
        try:
            from core import canon as canon_mod
            from core import plant_hygiene as plant_hygiene_mod
            canon_text = paths.get_canon_path().read_text(encoding="utf-8")
            characters_text = paths.get_characters_path().read_text(encoding="utf-8")
            hy_ok, hy_err, hy_side = plant_hygiene_mod.validate_outline_plant_hygiene(
                outline_text, canon_text, characters_text
            )
            (paths.get_project_dir() / "plant_hygiene.json").write_text(
                json.dumps(hy_side, indent=2), encoding="utf-8"
            )
            if not hy_ok:
                step(f"WARNING: Outline plant hygiene failed:\n{hy_err}")
                # Block-level retries already ran in gen_outline. Here we only
                # surface the sidecar; regenerating the whole outline on a
                # late-block leak would throw away good earlier chapters.
            else:
                step(f"Plant hygiene OK (reveal={hy_side.get('reveal_chapter')})")
        except Exception as e:
            step(f"Plant hygiene check skipped: {e}")

        state.update(load_state())
        debts = extract_outline_debts(outline_text)
        state["debts"] = debts
        save_state(state)
        step(f"Logged {len(debts)} active narrative debts in project state.")

        step("Running voice fingerprint...")
        uv_run("pipeline/voice_fingerprint.py", timeout=600)

        # 2. Evaluate
        step("Evaluating foundation...")
        eval_result = uv_run("pipeline/evaluate.py --phase=foundation", timeout=300)
        score = parse_score(eval_result.stdout, "overall_score")
        lore = parse_lore_score(eval_result.stdout)

        step(f"Foundation score: {score}  (lore: {lore}, prev best: {best_score})")

        # 3. Keep or discard (strict improvement — ties are stalls, else a
        # flat-score judge never trips the plateau exit below)
        prev_best = best_score
        keep, best_score, stall_count = foundation_plateau(
            score, best_score, stall_count)
        if keep:
            commit_hash = git_add_commit(
                f"foundation iter {i}: score {score} (lore {lore})")
            log_result(commit_hash, "foundation", score, 0, "keep",
                       f"Iteration {i}: score improved {prev_best} -> {score}")
            state["foundation_score"] = score
            state["lore_score"] = lore
            save_state(state)
        else:
            step(f"Score did not improve ({score} <= {prev_best}), discarding")
            git_reset_hard("HEAD")
            log_result("discarded", "foundation", score, 0, "discard",
                       f"Iteration {i}: no improvement ({score} <= {prev_best})")

        state["foundation_stall_count"] = stall_count
        save_state(state)

        # 4. Check exit conditions: threshold pass, or plateau — the judge
        # prompts instruct it to revise down scores above 7, so a harsh genre
        # rubric can make the threshold structurally unreachable (observed:
        # identical 6.0 across 5 iterations). Proceed with best docs instead
        # of burning iterations.
        if best_score >= threshold:
            step(f"Foundation score {best_score} >= {threshold} — PASSED")
            break
        if stall_count >= FOUNDATION_PLATEAU_ITERS:
            step(f"Foundation PLATEAU: {stall_count} consecutive iterations without "
                 f"improvement (best {best_score} vs threshold {threshold}) — "
                 f"proceeding to drafting with the best docs. Lower "
                 f"GESAKU_FOUNDATION_THRESHOLD to keep pushing.")
            break
    else:
        step(f"WARNING: max iterations ({MAX_FOUNDATION_ITERS}) reached "
             f"with score {best_score}")

    # Determine total chapters from state (preset by genre config in run_pipeline)
    total = state.get("chapters_total", CHAPTERS_TOTAL)
    state["chapters_total"] = total
    state["phase"] = "drafting"
    state["current_focus"] = "chapter_drafting"
    save_state(state)

    banner(f"FOUNDATION COMPLETE — score {best_score}, {total} chapters planned")
    return state


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
        rep = run_tool(cmd, timeout=120, check=False)
        if rep.returncode != 0:
            step(f"micro-plant extract skipped for ch{ch} (rc={rep.returncode})")
    except Exception as e:
        step(f"micro-plant extract failed for ch{ch}: {e}")


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


# ---------------------------------------------------------------------------
# PHASE 2 — DRAFTING
# ---------------------------------------------------------------------------

def _maybe_run_reveal_retrofit(state: dict, ch: int) -> None:
    """After the reveal chapter is kept, retrofit ch 1..R-1 once.

    Under-planted outlines block the pass (see pipeline/retrofit_reveal.py).
    Never retries in a loop — state flag marks completion either way.
    """
    if state.get("reveal_retrofit_done"):
        return
    try:
        from core import canon as canon_mod
        canon_path = paths.get_canon_path()
        if not canon_path.exists():
            return
        parsed = canon_mod.parse_canon(canon_path.read_text(encoding="utf-8"))
        reveal = parsed.reveal_chapter()
        if not reveal or ch != reveal:
            return
        step(f"Reveal chapter {reveal} kept — running post-reveal retrofit...")
        result = uv_run("pipeline/retrofit_reveal.py", timeout=1800)
        state["reveal_retrofit_done"] = True
        state["reveal_retrofit_exit"] = result.returncode
        save_state(state)
        if result.returncode == 2:
            step("Retrofit BLOCKED (under-planted). See retrofit_report.json — "
                 "continuing; revision cycles can still polish early chapters.")
        elif result.returncode != 0:
            step(f"Retrofit exited {result.returncode}; continuing drafting.")
        else:
            git_add_commit(f"retrofit: post-reveal pass through ch{reveal - 1}")
            step("Retrofit complete.")
    except Exception as e:
        step(f"Reveal retrofit skipped due to error: {e}")
        state["reveal_retrofit_done"] = True
        save_state(state)


def run_drafting(state: dict) -> dict:
    """
    Draft each chapter sequentially, evaluating and retrying as needed.
    """
    banner("PHASE 2: DRAFTING", "=")

    total = get_total_chapters(state)
    start_chapter = state.get("chapters_drafted", 0) + 1

    # Hard word-count floor for accepting a draft (below the eval's 80% tolerance,
    # a chapter is structurally broken — 100 bytes (~15 words) is not a draft).
    try:
        genre_cfg = load_genre()
        est_words = genre_cfg["generation"]["outline"]["estimated_words"]
        target_words = est_words // total
    except (KeyError, ZeroDivisionError):
        target_words = 3200
    min_words = int(target_words * 0.6)
    step(f"Chapter target: ~{target_words} words (min acceptable draft: {min_words})")

    # Hard floor for force-keeping a failed chapter: below this we skip and record,
    # we do NOT ship sub-garbage as canon.
    chapter_gate = chapter_threshold()
    max_attempts = max_chapter_attempts()
    force_keep_floor = chapter_gate - 2.0

    chapters_dir = paths.get_chapters_dir()  # also creates the directory

    for ch in range(start_chapter, total + 1):
        banner(f"Drafting Chapter {ch}/{total}", "-")
        drafted = False
        best_score = -1.0
        best_draft_content = None
        best_word_count = 0
        best_attempt_num = 0
        attempt_log_paths = {}  # attempt_num -> eval log path for that attempt
        retry_feedback = ""
        slop_repaired = False

        for attempt in range(1, max_attempts + 1):
            step(f"Attempt {attempt}/{max_attempts}")
            # Inner infra-retry loop: timeouts, empty files, and truncations don't burn quality attempts
            quality_attempt = False
            for infra in range(1, INFRA_MAX_ATTEMPTS + 1):
                cmd = f"\"{sys.executable}\" pipeline/draft_chapter.py {ch}"
                if retry_feedback:
                    # Quote-safe: shlex.split() in run_tool handles spaces, but
                    # the feedback may contain newlines/quotes -- write to a
                    # temp file and pass the path instead.
                    fb_path = paths.get_project_dir() / f"retry_feedback_ch{ch:02d}.txt"
                    fb_path.write_text(retry_feedback, encoding="utf-8")
                    cmd = (f"\"{sys.executable}\" pipeline/draft_chapter.py {ch} "
                           f"--retry-feedback \"{fb_path}\"")
                draft_result = run_tool(cmd, timeout=900, check=False)
                if draft_result.returncode != 0:
                    step(f"Draft failed (exit {draft_result.returncode}), retrying...")
                    continue

                ch_file = chapters_dir / f"ch_{ch:02d}.md"
                if not ch_file.exists() or ch_file.stat().st_size < 100:
                    step("Chapter file missing or too short, retrying...")
                    continue
                word_count = len(ch_file.read_text(encoding="utf-8").split())
                if word_count < min_words:
                    # Chronic undershoot (observed: 15 consecutive Ch-1 drafts
                    # below the floor). Retry with explicit expansion numbers —
                    # a bare "too short" gives the writer nothing to act on.
                    step(f"Chapter too short ({word_count}w < {min_words}w minimum), "
                         f"retrying with expansion budget...")
                    genre_cfg = load_genre()
                    target = (genre_cfg["generation"]["outline"]["estimated_words"]
                              // max(genre_cfg["generation"]["outline"]["estimated_chapters"], 1))
                    shortfall = target - word_count
                    retry_feedback = (
                        f"LENGTH FIX REQUIRED: your previous draft was only {word_count} words; "
                        f"the target is ~{target} words (shortfall: {shortfall}).\n"
                        f"Do NOT add filler or summarize faster. Instead: expand EVERY scene beat "
                        f"to full scene treatment — dramatize the beats you compressed, add sensory "
                        f"detail, physical action, and real-time interiority per beat. "
                        f"Aim for at least {min_words} words this time."
                    )
                    continue

                quality_attempt = True
                break

            if not quality_attempt:
                step(f"Max infra retries ({INFRA_MAX_ATTEMPTS}) exceeded — quality attempt {attempt} counts as failed")
                continue

            word_count = len(ch_file.read_text(encoding="utf-8").split())
            step(f"Drafted {word_count} words")

            # Evaluate
            eval_result = uv_run(f"pipeline/evaluate.py --chapter={ch}", timeout=300)
            score = parse_score(eval_result.stdout, "overall_score")
            step(f"Chapter {ch} score: {score}")

            # Pin the exact eval log of THIS attempt (evaluate.py prints 'eval_log: <path>')
            eval_log_path = None
            log_m = re.search(r"eval_log:\s*(\S+)", eval_result.stdout)
            if log_m:
                eval_log_path = Path(log_m.group(1))
                attempt_log_paths[attempt] = eval_log_path

            if score >= chapter_gate:
                fb_path = paths.get_project_dir() / f"retry_feedback_ch{ch:02d}.txt"
                fb_path.unlink(missing_ok=True)
                commit_hash = git_add_commit(
                    f"ch{ch:02d}: score {score}, {word_count}w")
                log_result(commit_hash, f"ch{ch:02d}", score, word_count,
                           "keep", f"Chapter {ch} (attempt {attempt})")
                state["chapters_drafted"] = ch
                save_state(state)

                # Append canon entries from the eval JSON LOG FILE
                update_canon_from_eval(ch, attempt_num=attempt, eval_log_path=eval_log_path)
                on_chapter_kept(ch)
                _maybe_run_reveal_retrofit(state, ch)

                drafted = True
                break
            else:
                if score > best_score:
                    best_score = score
                    best_draft_content = ch_file.read_text(encoding="utf-8")
                    best_word_count = word_count
                    best_attempt_num = attempt
                    step(f"New best fallback score for Ch {ch}: {score}")

                step(f"Score {score} < {chapter_gate}, discarding attempt")
                log_result("discarded", f"ch{ch:02d}", score, word_count,
                           "discard", f"Chapter {ch} attempt {attempt}")
                # Feed the judge's findings back into the next attempt
                retry_feedback, near_clean = build_eval_feedback(eval_log_path)
                if retry_feedback:
                    step(f"Built retry feedback for Ch {ch} attempt {attempt + 1} "
                         f"({len(retry_feedback)} chars)")

                if near_clean:
                    # The draft missed the keep bar by a hair with negligible
                    # mechanical penalties. Retrying means deleting this draft
                    # and generating blind — which regresses (observed: ch20
                    # 6.4 -> 3.32/4.5/3.24, ch13 6.25 -> 4.22). Keep it.
                    raw_note = ""
                    if eval_log_path and eval_log_path.exists():
                        try:
                            raw_note = str(json.loads(
                                eval_log_path.read_text(encoding="utf-8")
                            ).get("raw_judge_score", "?"))
                        except Exception:
                            raw_note = "?"
                    step(f"NEAR-CLEAN eval (raw {raw_note}) — keeping Ch {ch} at {score} "
                         f"instead of retrying")
                    commit_hash = git_add_commit(
                        f"ch{ch:02d}: near-clean keep, score {score}, {word_count}w")
                    log_result(commit_hash, f"ch{ch:02d}", score, word_count,
                               "keep", f"Chapter {ch} (near-clean keep, attempt {attempt})")
                    state["chapters_drafted"] = ch
                    save_state(state)
                    update_canon_from_eval(ch, attempt_num=attempt, eval_log_path=eval_log_path)
                    on_chapter_kept(ch)
                    _maybe_run_reveal_retrofit(state, ch)
                    drafted = True
                    break

                # TARGETED SLOP REPAIR: if the draft's content is fine (high raw
                # judge score) but mechanical slop penalties dragged it under the
                # bar, repair the flagged paragraphs IN PLACE instead of throwing
                # the draft away and regenerating blind (which regresses raw
                # quality — observed ch15 8.5-raw attempts bouncing 6.26->3.39).
                if not slop_repaired and eval_log_path and eval_log_path.exists():
                    try:
                        ev = json.loads(eval_log_path.read_text(encoding="utf-8"))
                        raw_judge = ev.get("raw_judge_score", 0) or 0
                        slop = ev.get("slop") or {}
                        mech = (slop.get("slop_penalty", 0) or 0) + (slop.get("prose_tic_penalty", 0) or 0)
                    except Exception:
                        raw_judge, mech = 0, 0
                    if raw_judge >= 7.0 and mech >= 1.5 and raw_judge - score >= 1.0:
                        slop_repaired = True
                        step(f"SLOP-DOMINANT eval (raw {raw_judge}, mech -{mech:.1f}) — "
                             f"repairing Ch {ch} in place instead of regenerating")
                        rep = run_tool(
                            f"\"{sys.executable}\" pipeline/repair_slop.py {ch}",
                            timeout=600, check=False)
                        if rep.returncode == 0:
                            rep_wc = len(ch_file.read_text(encoding="utf-8").split())
                            step(f"Repaired Ch {ch} ({rep_wc}w) — re-evaluating...")
                            rep_eval = uv_run(f"pipeline/evaluate.py --chapter={ch}", timeout=300)
                            rep_score = parse_score(rep_eval.stdout, "overall_score")
                            step(f"Repaired Ch {ch} score: {rep_score}")
                            if rep_score >= chapter_gate:
                                step(f"Repair lifted Ch {ch} over the bar — keeping")
                                fb_path = paths.get_project_dir() / f"retry_feedback_ch{ch:02d}.txt"
                                fb_path.unlink(missing_ok=True)
                                commit_hash = git_add_commit(
                                    f"ch{ch:02d}: slop-repair keep, score {rep_score}, {rep_wc}w")
                                log_result(commit_hash, f"ch{ch:02d}", rep_score, rep_wc,
                                           "keep", f"Chapter {ch} (slop repair, attempt {attempt})")
                                state["chapters_drafted"] = ch
                                save_state(state)
                                update_canon_from_eval(ch, attempt_num=attempt, eval_log_path=eval_log_path)
                                on_chapter_kept(ch)
                                _maybe_run_reveal_retrofit(state, ch)
                                drafted = True
                                break
                            elif rep_score > best_score and rep_score > 0:
                                best_score = rep_score
                                best_draft_content = ch_file.read_text(encoding="utf-8")
                                best_word_count = rep_wc
                                best_attempt_num = attempt
                                step(f"Repaired Ch {ch} is new best fallback: {rep_score}")

                # Remove the bad chapter file so next attempt starts fresh
                if ch_file.exists():
                    rel_path = f"chapters/ch_{ch:02d}.md"
                    res = subprocess.run(
                        shlex.split(f"git ls-files --error-unmatch {rel_path}"),
                        cwd=str(paths.get_project_dir()),
                        capture_output=True,
                        text=True,
                        shell=False
                    )
                    if res.returncode == 0:
                        run_tool(f"git checkout -- {rel_path}", cwd=str(paths.get_project_dir()))
                    else:
                        ch_file.unlink(missing_ok=True)

        if not drafted:
            force_worthy = (
                best_draft_content is not None
                and best_score >= force_keep_floor
                and best_word_count >= min_words
            )
            if force_worthy:
                step(f"WARNING: Chapter {ch} failed all {max_attempts} attempts, "
                     f"keeping best attempt {best_attempt_num} (score {best_score}, floor {force_keep_floor}) and moving on")
                ch_file = chapters_dir / f"ch_{ch:02d}.md"
                ch_file.write_text(best_draft_content, encoding="utf-8")
                commit_hash = git_add_commit(
                    f"ch{ch:02d}: best-effort (score {best_score}, attempt {best_attempt_num}) after {max_attempts} attempts")
                log_result(commit_hash, f"ch{ch:02d}", best_score, best_word_count,
                           "forced", f"Chapter {ch}: kept best-effort after max attempts")
                state["chapters_drafted"] = ch
                save_state(state)
                # Append canon entries of the best attempt even when force-kept
                update_canon_from_eval(ch, attempt_num=best_attempt_num,
                                       eval_log_path=attempt_log_paths.get(best_attempt_num))
                on_chapter_kept(ch)
                _maybe_run_reveal_retrofit(state, ch)
            else:
                if best_draft_content is None:
                    reason = "no valid drafts were generated"
                else:
                    reason = (f"best score {best_score} below force-keep floor {force_keep_floor} "
                              f"or word count {best_word_count} below {min_words}")
                step(f"WARNING: Chapter {ch} failed all {max_attempts} attempts ({reason}). "
                     f"Marking chapter as SKIPPED in state — it will be absent from the manuscript.")
                (paths.get_project_dir() / f"retry_feedback_ch{ch:02d}.txt").unlink(missing_ok=True)
                log_result("skipped", f"ch{ch:02d}", best_score, best_word_count,
                           "skipped", reason)
                state["chapters_drafted"] = ch
                skipped = state.get("skipped_chapters", [])
                if ch not in skipped:
                    skipped.append(ch)
                state["skipped_chapters"] = skipped
                save_state(state)

    # All chapters drafted
    state["phase"] = "revision"
    state["current_focus"] = "full_novel"
    state["chapters_drafted"] = total
    state["revision_cycle"] = 0
    save_state(state)

    total_words = count_words_in_chapters()
    banner(f"DRAFTING COMPLETE — {total} chapters, {total_words} words")
    return state


# ---------------------------------------------------------------------------
# PHASE 3 — REVISION
# ---------------------------------------------------------------------------

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
                  and adjusted_score >= chapter_threshold() - 1.0
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


def parse_panel_consensus(panel_path: Path) -> list[dict]:
    """
    Parse reader_panel.json to find chapters with consensus issues.
    Returns list of dicts: {chapter, question, flagged_by, details}
    sorted by number of readers who flagged (descending).
    """
    if not panel_path.exists():
        return []
    with open(panel_path) as f:
        data = json.load(f)

    items = []

    # Look at disagreements — these are flagged by some but not all readers
    for d in data.get("disagreements", []):
        items.append({
            "chapter": d.get("chapter", 0),
            "question": d.get("question", ""),
            "flagged_by": d.get("flagged_by", []),
            "count": len(d.get("flagged_by", [])),
        })

    # Also scan readers for direct chapter mentions in key questions
    readers = data.get("readers", {})
    chapter_mentions = {}  # ch_num -> count of readers mentioning it

    for reader_key, answers in readers.items():
        for question in ["momentum_loss", "cut_candidate", "worst_scene",
                         "thinnest_character", "missing_scene"]:
            answer = answers.get(question, "")
            if not isinstance(answer, str):
                continue
            chs = re.findall(r'Ch(?:apter)?\s*(\d+)', answer, re.IGNORECASE)
            for ch_str in chs:
                ch_num = int(ch_str)
                key = (ch_num, question)
                if key not in chapter_mentions:
                    chapter_mentions[key] = {"chapter": ch_num, "question": question,
                                             "flagged_by": [], "count": 0}
                chapter_mentions[key]["flagged_by"].append(reader_key)
                chapter_mentions[key]["count"] += 1

    # Merge and deduplicate
    seen = set()
    for item in items:
        seen.add((item["chapter"], item["question"]))
    for key, item in chapter_mentions.items():
        if key not in seen:
            items.append(item)

    # Sort by count descending, take unique chapters
    items.sort(key=lambda x: -x["count"])

    # Deduplicate by chapter (keep highest-count issue per chapter)
    seen_chapters = set()
    unique = []
    for item in items:
        if item["chapter"] not in seen_chapters and item["chapter"] > 0:
            seen_chapters.add(item["chapter"])
            unique.append(item)

    return unique[:5]  # top 3-5 consensus items


def run_revision(
    state: dict,
    max_cycles: int | None = None,
    skip_adversarial_editing: bool = False,
    skip_mechanical_cuts: bool = False,
    skip_reader_panel: bool = False,
    skip_targeted_revisions: bool = False,
    skip_full_novel_eval: bool = False,
    skip_opus_review: bool = False
) -> dict:
    """
    Revision phase: adversarial editing, reader panel, targeted revisions.
    """
    banner("PHASE 3: REVISION", "=")

    if max_cycles is None:
        max_cycles = max_revision_cycles()

    briefs_dir = paths.get_briefs_dir()        # also creates the directory
    edit_logs_dir = paths.get_edit_logs_dir()  # also creates the directory

    prev_score = state.get("novel_score")  # None = never scored
    start_cycle = state.get("revision_cycle", 0) + 1
    tolerance = 0.8

    for cycle in range(start_cycle, max_cycles + 1):
        banner(f"Revision Cycle {cycle}/{max_cycles}", "-")

        # Check if we should run adversarial editing or mechanical cuts
        run_adv = not skip_adversarial_editing
        apply_cuts = paths.get_root_dir() / "apply_cuts.py"
        run_cuts = not skip_mechanical_cuts and apply_cuts.exists()

        if run_adv or run_cuts:
            # Evaluate current baseline score (before Step 1/2 edits)
            step("Evaluating baseline novel score before Cycle edits...")
            baseline_eval = uv_run("pipeline/evaluate.py --full", timeout=600)
            cycle_baseline_score = parse_score(baseline_eval.stdout, "novel_score")
            if cycle_baseline_score < 0:
                cycle_baseline_score = parse_score(baseline_eval.stdout, "overall_score")

            post_adv_score = cycle_baseline_score
            if run_adv:
                # -- Step 1: Adversarial editing pass (parallel per chapter) --
                step("Running adversarial editing on all chapters...")
                total_ch = get_total_chapters(state)
                # Parallelism: default 4 workers even for local proxies —
                # build_arc_summary/build_outline already run 4-12 concurrent
                # LLM calls against the same endpoint. Override with
                # GESAKU_MAX_WORKERS (e.g. =1 for weak single-request models).
                max_workers = int(os.getenv("GESAKU_MAX_WORKERS", "4"))
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {
                        pool.submit(uv_run, f"adversarial_edit.py {ch}", 600): ch
                        for ch in range(1, total_ch + 1)
                    }
                    for future in as_completed(futures):
                         ch = futures[future]
                         try:
                             future.result()
                             step(f"  ch {ch}: done")
                         except Exception:
                             step(f"  ch {ch}: edit failed, continuing anyway")
                
                # Evaluate full novel score after Step 1
                step("Evaluating novel score after Adversarial Edits...")
                post_adv_eval = uv_run("pipeline/evaluate.py --full", timeout=600)
                post_adv_score = parse_score_any(post_adv_eval.stdout, "novel_score", "overall_score")
                
                step(f"Adversarial edits score shift: {cycle_baseline_score} -> {post_adv_score}")
                
                # Validation check with tolerance
                if post_adv_score >= (cycle_baseline_score - tolerance):
                    run_tool("git add -A", cwd=str(paths.get_project_dir()))
                    commit_hash = git_add_commit(
                        f"revision cycle {cycle}: apply adversarial edits {cycle_baseline_score}->{post_adv_score}"
                    )
                    log_result(commit_hash, f"rev-cycle-{cycle}-adv", post_adv_score,
                               count_words_in_chapters(), "keep",
                               f"Cycle {cycle}: Step 1 adversarial edits kept {cycle_baseline_score}->{post_adv_score}")
                else:
                    step(f"Adversarial edits made the novel significantly worse ({post_adv_score} < {cycle_baseline_score - tolerance}), reverting edits")
                    git_reset_hard("HEAD")
                    post_adv_score = cycle_baseline_score
                    log_result("reverted", f"rev-cycle-{cycle}-adv", post_adv_score,
                               count_words_in_chapters(), "discard",
                               f"Cycle {cycle}: Step 1 adversarial edits regressed {cycle_baseline_score}->{post_adv_score}")
            else:
                step("Skipping adversarial editing as requested")

            if run_cuts:
                # -- Step 2: Apply mechanical cuts --
                step("Applying mechanical cuts (OVER-EXPLAIN, REDUNDANT)...")
                run_tool("uv run python pipeline/apply_cuts.py all "
                         "--types OVER-EXPLAIN REDUNDANT --min-fat 15", timeout=300)
                
                # Evaluate full novel score after Step 2
                step("Evaluating novel score after Mechanical Cuts...")
                post_cuts_eval = uv_run("pipeline/evaluate.py --full", timeout=600)
                post_cuts_score = parse_score_any(post_cuts_eval.stdout, "novel_score", "overall_score")

                step(f"Mechanical cuts score shift: {post_adv_score} -> {post_cuts_score}")
                
                if post_cuts_score >= (post_adv_score - 0.05):
                    run_tool("git add -A", cwd=str(paths.get_project_dir()))
                    commit_hash = git_add_commit(
                        f"revision cycle {cycle}: apply mechanical cuts {post_adv_score}->{post_cuts_score}"
                    )
                    log_result(commit_hash, f"rev-cycle-{cycle}-cuts", post_cuts_score,
                               count_words_in_chapters(), "keep",
                               f"Cycle {cycle}: Step 2 mechanical cuts kept {post_adv_score}->{post_cuts_score}")
                    store_novel_score(state, post_cuts_score)
                else:
                    step(f"Mechanical cuts made the novel worse ({post_cuts_score} < {post_adv_score - 0.05}), reverting cuts")
                    git_reset_hard("HEAD")
                    log_result("reverted", f"rev-cycle-{cycle}-cuts", post_cuts_score,
                               count_words_in_chapters(), "discard",
                               f"Cycle {cycle}: Step 2 mechanical cuts regressed {post_adv_score}->{post_cuts_score}")
                    store_novel_score(state, post_adv_score)
            else:
                if skip_mechanical_cuts:
                    step("Skipping mechanical cuts as requested")
                else:
                    step("apply_cuts.py not found, skipping mechanical cuts")
                store_novel_score(state, post_adv_score)
        else:
            step("Skipping both adversarial editing and mechanical cuts — no Cycle edits to apply")

        # -- Step 3: Generate arc summary + Reader panel --
        if not skip_reader_panel:
            step("Generating arc summary for reader panel...")
            # 4 workers × 120s per chapter call; cap must cover all chapters
            n_ch_arc = count_chapter_files()
            uv_run("pipeline/build_arc_summary.py", timeout=max(300, -(-n_ch_arc // 4) * 150 + 60))
            step("Running reader panel evaluation...")
            # 4 sequential readers × (300s call + retries) — don't kill a slow panel
            uv_run("pipeline/reader_panel.py", timeout=1800)
        else:
            step("Skipping reader panel as requested")

        # -- Step 4: Parse panel consensus --
        panel_path = edit_logs_dir / "reader_panel.json"
        if not skip_reader_panel:
            consensus_items = parse_panel_consensus(panel_path)
        else:
            if panel_path.exists():
                consensus_items = parse_panel_consensus(panel_path)
            else:
                consensus_items = []

        # -- Step 5: Targeted revisions for consensus items (parallel) --
        if not skip_targeted_revisions and consensus_items:
            step(f"Found {len(consensus_items)} consensus items:")
            for item in consensus_items:
                print(f"    Ch {item['chapter']}: {item['question']} "
                      f"(flagged by {item['count']} readers)")

            def _revise_one(item):
                """Revise one chapter (brief + revision + eval). No git ops."""
                ch_num = item["chapter"]
                question = item["question"]
                try:
                    pre_eval = uv_run(f"pipeline/evaluate.py --chapter={ch_num}", timeout=300)
                    pre_score = parse_score(pre_eval.stdout, "overall_score")

                    brief_file = briefs_dir / f"ch{ch_num:02d}_cycle{cycle}_{question}.md"
                    gen_brief_py = paths.get_root_dir() / "pipeline" / "gen_brief.py"
                    if gen_brief_py.exists():
                        run_tool(f"uv run python pipeline/gen_brief.py --panel {ch_num}", timeout=300)
                        brief_candidates = sorted(
                            briefs_dir.glob(f"ch{ch_num:02d}*.md"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
                        if brief_candidates:
                            brief_file = brief_candidates[0]
                    else:
                        brief_content = (
                            f"# Revision Brief: Chapter {ch_num}\n\n"
                            f"## Issue: {question}\n\n"
                            f"Panel consensus identified this chapter for revision.\n"
                            f"Focus: address the {question.replace('_', ' ')} issue.\n"
                            f"Preserve existing voice, character work, and essential beats.\n"
                        )
                        brief_file.write_text(brief_content, encoding="utf-8")

                    if not brief_file.exists():
                        return {"ch_num": ch_num, "error": "no brief file",
                                "pre_score": pre_score, "post_score": pre_score}

                    step(f"Revising Ch {ch_num} with brief {brief_file.name}...")
                    uv_run(f'pipeline/gen_revision.py {ch_num} "{brief_file}"', timeout=600)

                    post_eval = uv_run(f"pipeline/evaluate.py --chapter={ch_num}", timeout=300)
                    post_score = parse_score(post_eval.stdout, "overall_score")
                    eval_log_path = None
                    m = re.search(r"eval_log:\s*(\S+)", post_eval.stdout)
                    if m:
                        eval_log_path = m.group(1)

                    ch_file = paths.get_chapters_dir() / f"ch_{ch_num:02d}.md"
                    word_count = len(ch_file.read_text(encoding="utf-8").split()) if ch_file.exists() else 0

                    hist_best_score, hist_best_commit = get_historical_best_for_chapter(ch_num)
                    baseline = max(pre_score, hist_best_score)

                    return {
                        "ch_num": ch_num,
                        "question": question,
                        "pre_score": pre_score,
                        "post_score": post_score,
                        "word_count": word_count,
                        "baseline": baseline,
                        "hist_best_commit": hist_best_commit,
                        "brief_name": brief_file.name,
                        "eval_log_path": eval_log_path,
                    }
                except Exception as e:
                    return {"ch_num": ch_num, "error": str(e)}

            # Parallelism: default 4 workers even for local proxies —
            # override with GESAKU_MAX_WORKERS (e.g. =1 for weak models).
            max_workers = int(os.getenv("GESAKU_MAX_WORKERS", "4"))
            max_workers = max(1, min(max_workers, len(consensus_items)))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_revise_one, item): item for item in consensus_items}
                results = []
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        r = future.result()
                        results.append(r)
                        if r.get("error"):
                            step(f"  Ch {r['ch_num']}: revision failed — {r['error']}")
                        else:
                            step(f"  Ch {r['ch_num']}: {r['pre_score']} -> {r['post_score']}")
                    except Exception as e:
                        step(f"  Ch {item['chapter']}: unexpected error — {e}")

            # Serialized: git add/commit or revert per chapter
            kept_this_cycle = []
            for r in sorted(results, key=lambda x: x["ch_num"]):
                if r.get("error"):
                    continue
                ch_num = r["ch_num"]
                if r["post_score"] >= (r["baseline"] - tolerance):
                    run_tool(f"git add chapters/ch_{ch_num:02d}.md", cwd=str(paths.get_project_dir()))
                    commit_hash = git_commit_staged(
                        f"revision cycle {cycle}: ch{ch_num:02d} "
                        f"{r['question']} {r['pre_score']}->{r['post_score']}")
                    log_result(commit_hash, f"rev-ch{ch_num:02d}", r["post_score"],
                               r["word_count"], "keep",
                               f"Cycle {cycle}: {r['question']} improved {r['pre_score']}->{r['post_score']}")
                    kept_this_cycle.append(r)
                else:
                    step(f"Ch {ch_num}: score dropped ({r['post_score']} < {r['baseline'] - tolerance}), reverting")
                    ch_file = paths.get_chapters_dir() / f"ch_{ch_num:02d}.md"
                    if r["hist_best_commit"] == "HEAD":
                        tracked_res = run_tool(
                            f"git ls-files --error-unmatch chapters/ch_{ch_num:02d}.md",
                            cwd=str(paths.get_project_dir())
                        )
                        if tracked_res.returncode == 0:
                            run_tool(f"git checkout HEAD -- chapters/ch_{ch_num:02d}.md", cwd=str(paths.get_project_dir()))
                        else:
                            ch_file.unlink(missing_ok=True)
                    else:
                        run_tool(f"git checkout {r['hist_best_commit']} -- chapters/ch_{ch_num:02d}.md", cwd=str(paths.get_project_dir()))
                    log_result("reverted", f"rev-ch{ch_num:02d}", r["post_score"],
                               r["word_count"], "discard",
                               f"Cycle {cycle}: {r['question']} regressed {r['pre_score']}->{r['post_score']}")
            if kept_this_cycle:
                resync_canon_after_cycle(kept_this_cycle, cycle)
                for r in kept_this_cycle:
                    on_chapter_kept(r["ch_num"], reextract=True)
        elif not skip_targeted_revisions:
            step("No strong consensus items found from panel")
        else:
            step("Skipping targeted revisions as requested")

        # -- Step 6: Full novel evaluation --
        if not skip_full_novel_eval:
            step("Running full novel evaluation...")
            full_eval = uv_run("pipeline/evaluate.py --full", timeout=600)
            try:
                novel_score = parse_score_any(full_eval.stdout, "novel_score", "overall_score")
            except ValueError as e:
                step(f"WARNING: could not parse any score from full eval — keeping previous score. {e}")
                novel_score = prev_score

            if novel_score is None or novel_score <= 0.0:
                # 0.0 is almost always a judge failure — retry once
                step("Novel score missing/0.0 detected, retrying evaluation...")
                retry_eval = uv_run("pipeline/evaluate.py --full", timeout=600)
                try:
                    novel_score = parse_score_any(retry_eval.stdout, "novel_score", "overall_score")
                except ValueError as e:
                    step(f"WARNING: retry eval unparseable — keeping previous score. {e}")
                    novel_score = prev_score
                if novel_score is None or novel_score <= 0.0:
                    step("Novel score still unusable after retry — keeping previous score")
                    novel_score = prev_score
        else:
            step("Skipping full novel evaluation as requested")
            novel_score = prev_score

        total_words = count_words_in_chapters()
        step(f"Novel score: {fmt_score(novel_score)}  (prev: {fmt_score(prev_score)}, words: {total_words})")

        # Commit cycle results
        commit_hash = git_add_commit(
            f"revision cycle {cycle} complete: novel_score {fmt_score(novel_score)}")
        log_result(commit_hash, f"revision-cycle-{cycle}", fmt_score(novel_score),
                   total_words, "cycle",
                   f"Cycle {cycle}: novel_score {fmt_score(prev_score)}->{fmt_score(novel_score)}")

        stored = store_novel_score(state, novel_score)
        state["revision_cycle"] = cycle
        save_state(state)

        # -- Step 7: Plateau detection --
        if not skip_full_novel_eval:
            plateau = plateau_delta()
            min_cycles = min_revision_cycles()
            comparable = (
                cycle >= min_cycles
                and stored is not None
                and prev_score is not None
                and abs(stored - prev_score) < plateau
            )
            if comparable:
                # Secondary gate: don't stop while >30% of chapters are below threshold
                total_ch = get_total_chapters(state)
                below = 0
                with_history = 0
                for cn in range(1, total_ch + 1):
                    last_score, hist_commit = get_historical_best_for_chapter(cn)
                    if hist_commit == "HEAD" and last_score == 0.0:
                        continue  # no history yet, skip
                    with_history += 1
                    if last_score < chapter_threshold():
                        below += 1
                pct_below = below / with_history * 100 if with_history > 0 else 0
                if pct_below > 30:
                    step(f"Plateau suppressed: {below}/{with_history} scored chapters below threshold ({pct_below:.0f}% > 30%) — continuing revision")
                else:
                    step(f"Plateau detected (delta {abs(stored - prev_score):.2f} "
                         f"< {plateau}) after {cycle} cycles — stopping")
                    break

        if stored is not None:
            prev_score = stored

    # =========================================================
    # PHASE 3b: OPUS REVIEW LOOP (deep, prose-level refinement)
    # =========================================================
    review_py = paths.get_root_dir() / "pipeline" / "review.py"
    if not skip_opus_review and review_py.exists():
        banner("PHASE 3b: OPUS REVIEW LOOP", "=")
        
        max_review_rounds = 4
        for rnd in range(1, max_review_rounds + 1):
            banner(f"Opus Review Round {rnd}/{max_review_rounds}", "-")
            
            # Step 1: Generate the review
            step("Sending manuscript to Opus for review...")
            review_result = uv_run(
                f'pipeline/review.py --output "{paths.get_reviews_path()}"', timeout=900)
            
            # Step 2: Parse the review
            step("Parsing review...")
            parse_result = run_tool(
                "uv run python pipeline/review.py --parse", timeout=60)
            print(parse_result.stdout if parse_result else "")
            
            # Step 3: Check stopping condition (uses review.py's should_stop)
            review_logs = sorted(
                paths.get_edit_logs_dir().glob("*_review.json"), reverse=True)
            total_items = 0
            if review_logs:
                try:
                    review_data = json.loads(review_logs[0].read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as e:
                    step(f"WARNING: review log {review_logs[0].name} is corrupt ({e}) — treating as no-op round")
                    review_data = {}

                stars = review_data.get("stars", 0) or 0
                total_items = review_data.get("total_items", 0)
                major_items = review_data.get("major_items", 0)
                qualified = review_data.get("qualified_items", 0)
                
                step(f"Stars: {stars}, Items: {total_items} "
                     f"({major_items} major, {qualified} qualified)")
                
                should_stop, reason = review_should_stop(review_data)
                if should_stop:
                    step(f"Stop revising? YES — {reason}")
                    break
            
            # Step 4: Generate briefs from review items and fix (skip if no items found)
            if total_items == 0:
                step("No actionable items from review — skipping revision, running mechanical cleanup only")
            else:
                step("Generating revision briefs from review...")
                gen_brief_py = paths.get_root_dir() / "pipeline" / "gen_brief.py"
                if gen_brief_py.exists():
                    run_tool("uv run python pipeline/gen_brief.py --auto", timeout=300)
                
                # Find any generated briefs and apply the top one
                recent_briefs = sorted(
                    paths.get_briefs_dir().glob("*_auto.md"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
                if recent_briefs:
                    brief = recent_briefs[0]
                    # Extract chapter number from filename
                    ch_match = re.search(r'ch(\d+)', brief.name)
                    if ch_match:
                        ch_num = int(ch_match.group(1))
                        
                        # Evaluate pre-revision score
                        step(f"Evaluating Ch {ch_num} before revision...")
                        pre_eval = uv_run(f"pipeline/evaluate.py --chapter={ch_num}", timeout=300)
                        pre_score = parse_score(pre_eval.stdout, "overall_score")
                        
                        step(f"Revising Ch {ch_num} from review brief...")
                        uv_run(f'pipeline/gen_revision.py {ch_num} "{brief}"', timeout=600)
                        
                        # Evaluate post-revision score
                        step(f"Evaluating Ch {ch_num} after revision...")
                        post_eval = uv_run(f"pipeline/evaluate.py --chapter={ch_num}", timeout=300)
                        post_score = parse_score(post_eval.stdout, "overall_score")
                        
                        # Compare against historical best
                        hist_best_score, hist_best_commit = get_historical_best_for_chapter(ch_num)
                        baseline = max(pre_score, hist_best_score)
                        
                        step(f"Ch {ch_num} Review Revision: {pre_score} -> {post_score} (Historical best: {hist_best_score}, Baseline: {baseline})")
                        
                        ch_file = paths.get_chapters_dir() / f"ch_{ch_num:02d}.md"
                        word_count = len(ch_file.read_text(encoding="utf-8").split()) if ch_file.exists() else 0
                        
                        if post_score >= (baseline - tolerance):
                            # Stage specifically
                            run_tool(f"git add chapters/ch_{ch_num:02d}.md", cwd=str(paths.get_project_dir()))
                            commit_hash = git_commit_staged(
                                f"review round {rnd}: revise ch{ch_num:02d} from Opus feedback {pre_score}->{post_score}")
                            log_result(commit_hash, f"rev-ch{ch_num:02d}-review", post_score,
                                       word_count, "keep",
                                       f"Round {rnd}: {brief.name} score {pre_score}->{post_score}")
                        else:
                            step(f"Review revision made it worse ({post_score} < {baseline - tolerance}). Reverting ch_{ch_num:02d} to best commit: {hist_best_commit}")
                            # Revert specifically
                            if hist_best_commit == "HEAD":
                                tracked_res = run_tool(
                                    f"git ls-files --error-unmatch chapters/ch_{ch_num:02d}.md",
                                    cwd=str(paths.get_project_dir())
                                )
                                if tracked_res.returncode == 0:
                                    run_tool(f"git checkout HEAD -- chapters/ch_{ch_num:02d}.md", cwd=str(paths.get_project_dir()))
                                else:
                                    ch_file.unlink(missing_ok=True)
                            else:
                                run_tool(f"git checkout {hist_best_commit} -- chapters/ch_{ch_num:02d}.md", cwd=str(paths.get_project_dir()))
                            log_result("reverted", f"rev-ch{ch_num:02d}-review", post_score,
                                       word_count, "discard",
                                       f"Round {rnd}: {brief.name} regressed {pre_score}->{post_score}")
            
            # Step 5: Mechanical fixes from review
            # Run slop pass on any mentioned patterns
            step("Running mechanical cleanup pass...")
            apply_cuts_py = paths.get_root_dir() / "apply_cuts.py"
            if apply_cuts_py.exists():
                # Evaluate score before cuts
                pre_cuts_eval = uv_run("pipeline/evaluate.py --full", timeout=600)
                pre_cuts_score = parse_score_any(pre_cuts_eval.stdout, "novel_score", "overall_score")

                run_tool(
                    "uv run python pipeline/apply_cuts.py all --types OVER-EXPLAIN REDUNDANT --min-fat 15",
                    timeout=300)

                # Evaluate score after cuts
                post_cuts_eval = uv_run("pipeline/evaluate.py --full", timeout=600)
                post_cuts_score = parse_score_any(post_cuts_eval.stdout, "novel_score", "overall_score")

                step(f"Mechanical cuts score shift: {pre_cuts_score} -> {post_cuts_score}")

                if post_cuts_score >= (pre_cuts_score - 0.05):
                    run_tool("git add -A", cwd=str(paths.get_project_dir()))
                    git_add_commit(f"review round {rnd}: mechanical cleanup {pre_cuts_score}->{post_cuts_score}")
                else:
                    step(f"Mechanical cuts made the novel worse ({post_cuts_score} < {pre_cuts_score - 0.05}), reverting cuts")
                    git_reset_hard("HEAD")
            
            step(f"Review round {rnd} complete.")
        
        banner("OPUS REVIEW LOOP COMPLETE")
    elif skip_opus_review:
        step("Skipping Opus review loop as requested")
    else:
        step("review.py not found, skipping Opus review loop")
    
    state["phase"] = "export"
    state["current_focus"] = "export"
    save_state(state)

    banner(f"REVISION COMPLETE — {state.get('revision_cycle', 0)} cycles, "
           f"novel_score {fmt_score(state.get('novel_score'))}")
    return state


# ---------------------------------------------------------------------------
# PHASE 4 — EXPORT
# ---------------------------------------------------------------------------




def run_export(state: dict) -> dict:
    """
    Build final deliverables: outline, arc summary, manuscript, PDF.
    """
    banner("PHASE 4: EXPORT", "=")

    import shutil
    root_dir = paths.get_root_dir()
    chapters_dir = paths.get_chapters_dir()
    typeset_dir = paths.get_typeset_dir()

    # 1. Rebuild outline from chapters
    build_outline = root_dir / "pipeline" / "build_outline.py"
    if build_outline.exists():
        step("Rebuilding outline from chapters...")
        uv_run("pipeline/build_outline.py", timeout=1200)

    # 2. Build arc summary
    build_arc = root_dir / "pipeline" / "build_arc_summary.py"
    if build_arc.exists():
        step("Building arc summary...")
        n_ch_arc = count_chapter_files()
        uv_run("pipeline/build_arc_summary.py", timeout=max(300, -(-n_ch_arc // 4) * 150 + 60))

    # 3. Pre-export cleanup: strip AI-tell formatting patterns for the EXPORTED
    #    deliverables only — the canonical chapter files are never mutated.
    #    (build_tex.py applies the same em-dash treatment for the PDF.)
    _EM_DASH_RE = re.compile(r'\u2014')                          # unicode em dash
    _BOLD_RE    = re.compile(r'\*\*(.+?)\*\*')                   # **bold** → plain

    def _export_clean(text: str) -> str:
        return _BOLD_RE.sub(r'\1', _EM_DASH_RE.sub(', ', text))

    # 4. Concatenate chapters into manuscript.md (written into project dir)
    step("Building manuscript.md...")
    manuscript = paths.get_manuscript_path()
    chapter_files = sorted(chapters_dir.glob("ch_*.md"), key=_chapter_num_key)

    total_planned = state.get("chapters_total", 0)
    if total_planned and len(chapter_files) < total_planned:
        present = {_chapter_num_key(p) for p in chapter_files}
        missing = [n for n in range(1, total_planned + 1) if n not in present]
        step(f"WARNING: {len(missing)} planned chapter(s) missing from manuscript: {missing}")

    parts = []
    for ch_file in chapter_files:
        text = _export_clean(ch_file.read_text(encoding="utf-8").strip())
        if text:
            parts.append(text)

    if parts:
        manuscript.write_text("\n\n---\n\n".join(parts) + "\n", encoding="utf-8")
        word_count = sum(len(p.split()) for p in parts)
        step(f"Manuscript: {len(parts)} chapters, {word_count} words")
    else:
        step("WARNING: no chapter files found for manuscript")

    # 4. Build LaTeX
    build_tex = root_dir / "typeset" / "build_tex.py"
    if build_tex.exists():
        step("Building LaTeX content...")
        # Run with cwd set to project typeset dir so aux files stay isolated
        run_tool(f'uv run python "{build_tex}"', timeout=120, cwd=str(paths.get_typeset_dir()))

        # 5. Typeset with tectonic (if available)
        novel_tex = typeset_dir / "novel.tex"
        tex_valid = (
            novel_tex.exists()
            and novel_tex.stat().st_size >= 100
            and "\\end{document}" in novel_tex.read_text(encoding="utf-8")
        )
        if not tex_valid:
            step("novel.tex not found, empty, or incomplete (no \\end{document}) — generating via LLM...")
            for tex_attempt in range(3):
                try:
                    uv_run("pipeline/gen_novel_tex.py", timeout=300)
                    if novel_tex.exists() and novel_tex.stat().st_size >= 100 and "\\end{document}" in novel_tex.read_text(encoding="utf-8"):
                        break
                except Exception as e:
                    step(f"LLM tex generation attempt {tex_attempt+1}/3 failed ({e})")
            if not novel_tex.exists() or novel_tex.stat().st_size < 100:
                step("Falling back to default template...")
                novel_tex_module.generate_default_novel_tex(novel_tex)

        compiled = False
        if novel_tex.exists():
            if shutil.which("tectonic"):
                # Ensure required fonts are installed before typesetting
                install_fonts_script = root_dir / "install_fonts.py"
                if install_fonts_script.exists():
                    step("Ensuring fonts are installed...")
                    uv_run("install_fonts.py", timeout=120)

                # Retry loop for tectonic compilation with LLM debugging
                max_latex_fixes = 3
                compiled = False
                for fix_attempt in range(max_latex_fixes + 1):
                    if fix_attempt > 0:
                        step(f"Retrying typesetting PDF (attempt {fix_attempt + 1}/{max_latex_fixes + 1})...")
                    else:
                        step("Typesetting PDF with tectonic...")

                    # Use explicit bundle to avoid DNS/network connection failure
                    cmd = f"tectonic --bundle https://archive.org/services/purl/net/pkgwpub/tectonic-default {novel_tex.name}"
                    res = run_tool(cmd, timeout=300, cwd=str(paths.get_typeset_dir()))
                    
                    pdf_out = typeset_dir / "novel.pdf"
                    if res.returncode == 0 and pdf_out.exists() and pdf_out.stat().st_size > 1000:
                        step(f"PDF generated: {pdf_out} ({pdf_out.stat().st_size // 1024} KB)")
                        compiled = True
                        break
                    
                    if fix_attempt >= max_latex_fixes:
                        break
                        
                    step(f"LaTeX compilation failed (exit code {res.returncode}).")
                    step("Attempting to auto-debug novel.tex using LLM with error logs...")
                    
                    # Call LLM to fix the LaTeX template
                    tex_code = novel_tex.read_text(encoding="utf-8")
                    
                    prompt = f"""The LaTeX file 'novel.tex' failed to compile using Tectonic.
                    
COMPILE LOGS / STDERR:
---
{res.stderr or '(no stderr)'}
---

CURRENT CONTENT OF 'novel.tex':
---
{tex_code}
---

Please analyze the compile log, identify the error (such as undefined control sequences, missing packages, syntax errors, or unescaped characters), and output the fully corrected, compile-ready version of 'novel.tex'. 

Rules:
1. Do NOT load fontspec. Use \\usepackage{{ebgaramond}} as defined.
2. Return ONLY the valid LaTeX code inside ```latex ... ``` fences. No conversational filler or explanations.
"""
                    try:
                        fixed_tex = call_llm(
                            prompt=prompt,
                            system="You are an expert LaTeX troubleshooter. You fix compilation errors and return only compile-ready corrected LaTeX code.",
                            model_key="review",
                            max_tokens=8000,
                            temperature=0.2,
                        )
                        # Extract from fences
                        m = re.search(r"```(?:latex|tex)?\s*\n(.*?)```", fixed_tex, re.DOTALL)
                        if m:
                            fixed_tex = m.group(1).strip()
                        else:
                            m2 = re.search(r"(\\documentclass[^]*?\\end\{document\})", fixed_tex, re.DOTALL)
                            if m2:
                                fixed_tex = m2.group(1).strip()
                        
                        if fixed_tex and len(fixed_tex) > 200:
                            novel_tex.write_text(fixed_tex, encoding="utf-8")
                            step("Wrote corrected novel.tex from LLM debugging.")
                        else:
                            step("WARNING: LLM returned invalid or empty LaTeX for fix.")
                    except Exception as e:
                        step(f"WARNING: LLM auto-debug API call failed: {e}")

                if not compiled:
                    # LLM debugging failed (observed: 3 attempts on a missing
                    # brace). Fall back to the deterministic default template —
                    # a known-good wrapper — and retry once before giving up.
                    step("LLM auto-debug failed to fix novel.tex. Falling back to "
                         "the deterministic default template...")
                    try:
                        novel_tex_module.generate_default_novel_tex(novel_tex)
                        res = run_tool(cmd, timeout=300, cwd=str(paths.get_typeset_dir()))
                        if res.returncode == 0 and pdf_out.exists() and pdf_out.stat().st_size > 1000:
                            step(f"PDF generated from default template: {pdf_out} "
                                 f"({pdf_out.stat().st_size // 1024} KB)")
                            compiled = True
                        else:
                            step("WARNING: default template also failed to typeset.")
                    except Exception as e:
                        step(f"WARNING: default-template fallback failed: {e}")

                if not compiled:
                    step("WARNING: tectonic typesetting failed — novel.pdf not produced")
            else:
                step("tectonic not found, skipping PDF generation")
    else:
        step("typeset/build_tex.py not found, skipping LaTeX")


    # 6. Final commit
    commit_hash = git_add_commit("export: manuscript, outline, arc summary, PDF")
    total_words = count_words_in_chapters()
    log_result(commit_hash, "export", fmt_score(state.get("novel_score")),
               total_words, "export", "Final export")

    if shutil.which("tectonic") and not compiled:
        state["phase"] = "complete_no_pdf"
    else:
        state["phase"] = "complete"
    state["current_focus"] = "done"
    save_state(state)

    banner(f"EXPORT COMPLETE — {len(chapter_files)} chapters, {total_words} words (Phase: {state['phase']})")
    return state


# ---------------------------------------------------------------------------
# Sanity check (pre-flight before any LLM call)
# ---------------------------------------------------------------------------

def sanity_check(args):
    """Run pre-flight checks. Exit 1 on critical failures."""
    ok = True
    notes_provided = bool(args.notes)
    root_dir = paths.get_root_dir()

    # 1. .env exists
    if not (root_dir / ".env").exists():
        print("FAIL: .env not found — create one from .env.example", file=sys.stderr)
        ok = False

    # 2. API key loaded for the resolved provider (load_dotenv already called).
    # First-party endpoints always need a key -> FAIL; custom gateways
    # (OpenRouter/LiteLLM/Ollama) may be keyless -> WARN only.
    from core.llm import resolve_provider, KEY_ENV_VARS, BASE_URL_ENV_VARS, DEFAULT_BASE_URLS
    provider = resolve_provider("writer")
    api_key = os.getenv(KEY_ENV_VARS[provider], "")
    base = os.getenv(BASE_URL_ENV_VARS[provider], DEFAULT_BASE_URLS[provider])
    if not api_key:
        msg = f"FAIL: {KEY_ENV_VARS[provider]} not set in .env"
        if base == DEFAULT_BASE_URLS[provider]:
            print(msg, file=sys.stderr)
            ok = False
        else:
            print(f"WARN: {msg.replace('FAIL', '')} — continuing anyway (custom endpoint may be keyless)", file=sys.stderr)

    # 3. API endpoint reachable
    try:
        httpx.get(base, timeout=5)
    except Exception:
        print(f"WARN: {base} unreachable — continuing anyway", file=sys.stderr)

    # 4. At least one of seed.txt or --notes exists
    if not (root_dir / "seed.txt").exists() and not paths.get_seed_path().exists() and not notes_provided:
        print("FAIL: provide --notes or place a seed.txt in the project root or project folder", file=sys.stderr)
        ok = False

    # 5. Genre is specified (skip if already configured from a previous run)
    if not args.genre and not os.getenv("GESAKU_GENRE"):
        if not paths.get_active_genre_path().exists() and not (root_dir / "active_genre.json").exists():
            print("FAIL: provide --genre or set GESAKU_GENRE in .env", file=sys.stderr)
            ok = False

    # --- Warnings (non-fatal) ---

    # Chapters parseable
    if args.chapters:
        try:
            int(args.chapters)
        except ValueError:
            descriptive = {"short", "story", "novella", "novelette", "epic", "saga"}
            if not any(w in args.chapters.lower() for w in descriptive):
                print(f"WARN: --chapters '{args.chapters}' looks unusual", file=sys.stderr)

    # active_genre.json valid if present
    active_path = paths.get_active_genre_path()
    if not active_path.exists():
        active_path = root_dir / "active_genre.json"
    if active_path.exists():
        try:
            json.loads(active_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"WARN: {active_path.name} is corrupted — delete it and re-run", file=sys.stderr)

    # state.json valid if present and not in --from-scratch mode
    state_path = paths.get_state_path()
    if state_path.exists() and not args.from_scratch:
        try:
            json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("WARN: state.json is corrupted — use --from-scratch to reset", file=sys.stderr)

    if not ok:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_pipeline(args):
    """Run the full pipeline or a specific phase."""

    # Set active project FIRST so all path helpers resolve correctly
    paths.set_project_name(args.project)
    project_dir = paths.get_project_dir()
    project_dir.mkdir(parents=True, exist_ok=True)

    # Tee stdout/stderr to a per-run log file in projects/<name>/logs/
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    log_path = paths.get_logs_dir() / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_pipeline.log"
    log_fh = open(log_path, "w", encoding="utf-8")
    sys.stdout = Tee(log_fh, sys.stdout)
    sys.stderr = Tee(log_fh, sys.stderr)
    step(f"Pipeline log: {log_path}")

    # Git Option B: guard root .gitignore and init project repo
    ensure_gitignore_projects()
    ensure_project_git(project_dir)

    sanity_check(args)

    root_dir = paths.get_root_dir()

    # Load or initialize state
    if args.from_scratch:
        banner("STARTING FROM SCRATCH")
        import shutil

        # Clean up existing files in the project directory to prevent cross-contamination
        if project_dir.exists():
            for name in ["chapters", "briefs", "edit_logs", "eval_logs", "typeset"]:
                p = project_dir / name
                if p.is_dir():
                    try:
                        shutil.rmtree(p)
                    except Exception as e:
                        print(f"WARN: Failed to clean directory {name}: {e}", file=sys.stderr)
            for name in ["world.md", "characters.md", "outline.md", "canon.md", "manuscript.md", "arc_summary.md", "results.tsv", "state.json", "active_genre.json", "seed.txt"]:
                p = project_dir / name
                if p.is_file():
                    try:
                        p.unlink()
                    except Exception as e:
                        print(f"WARN: Failed to remove file {name}: {e}", file=sys.stderr)

        # Initialize project-specific seed
        seed_dest = paths.get_seed_path()
        if not args.notes:
            if not seed_dest.exists():
                global_seed = root_dir / "seed.txt"
                if global_seed.exists():
                    print(f"\n[WARNING][CONTAMINATION RISK] No project-specific seed.txt found. Copying global seed.txt to {seed_dest}.\n", file=sys.stderr)
                    shutil.copy2(global_seed, seed_dest)
                else:
                    print("ERROR: No seed.txt found in project directory or repository root, and no --notes provided.", file=sys.stderr)
                    sys.exit(1)

        state = default_state()
        # Write user-provided chapter count into state before banner
        if args.chapters:
            try:
                state["chapters_total"] = int(args.chapters)
            except ValueError:
                pass  # non-numeric string like "short story" — let genre framework resolve
        
        # Copy template voice.md file to project directory if it exists in root
        voice_template = root_dir / "fuel" / "voice.md"
        if voice_template.exists():
            shutil.copy2(voice_template, paths.get_voice_path())
            prose_mode = getattr(args, "prose_mode", "") or os.environ.get("GESAKU_PROSE_MODE", "")
            if prose_mode:
                from core.genre import load_prose_pack
                pack = load_prose_pack(prose_mode)
                if pack:
                    voice_path = paths.get_voice_path()
                    existing = voice_path.read_text(encoding="utf-8")
                    voice_path.write_text(
                        existing + f"\n\n---\n\n## Prose mode ({prose_mode})\n\n{pack}\n",
                        encoding="utf-8",
                    )
                
        save_state(state)
    else:
        state = load_state()

    # Sync chapters_total from genre config on every entry (not just foundation)
    # This prevents stale state.json values from persisting across resume runs.
    try:
        genre_cfg = load_genre()
        genre_total = genre_cfg["generation"]["outline"]["estimated_chapters"]
        current_total = state.get("chapters_total", 0)
        if genre_total != current_total:
            state["chapters_total"] = genre_total
            save_state(state)
    except (FileNotFoundError, KeyError):
        pass  # pre-foundation — no genre config yet, use default or --chapters

    # Ensure directories exist (helpers create them)
    paths.get_chapters_dir()
    paths.get_briefs_dir()
    paths.get_edit_logs_dir()
    paths.get_eval_logs_dir()

    # Apply revision_cycles override (with legacy support for max_cycles)
    revision_cycles = args.max_cycles if args.max_cycles is not None else args.revision_cycles

    # Determine which phases to run
    if args.phase:
        # Single phase mode
        phases = [args.phase]
    else:
        # Run from current state onward
        current = state.get("phase", "foundation")
        if current == "complete_no_pdf":
            # PDF build failed on a prior run — resume from export so the run
            # doesn't re-execute foundation/drafting/revision destructively.
            current = "export"
        if current == "complete":
            print("Pipeline already complete. Use --from-scratch to restart "
                  "or --phase to run a specific phase.")
            return
        try:
            start_idx = PHASE_ORDER.index(current)
        except ValueError:
            start_idx = 0
        phases = PHASE_ORDER[start_idx:]

    banner(f"GESAKU PIPELINE — phases: {', '.join(phases)}")
    print(f"  State: phase={state.get('phase')}, "
          f"foundation_score={state.get('foundation_score', 0)}, "
          f"chapters={state.get('chapters_drafted', 0)}/{state.get('chapters_total', '?')}, "
          f"novel_score={fmt_score(state.get('novel_score'))}")

    start_time = datetime.now()

    for phase in phases:
        try:
            if phase == "foundation":
                global CHAPTERS_TOTAL

                # Step 0: Process user notes → auto-create seed.txt
                notes_for_genre = None
                if args.notes:
                    notes_for_genre = process_notes(args.notes, args.genre)
                seed_path = paths.get_seed_path()
                if not seed_path.exists():
                    print(f"ERROR: seed.txt not found at {seed_path}", file=sys.stderr)
                    sys.exit(1)
                # TODO: --continue mode — if pre-written chapters exist, generate
                # an outline that picks up from the last written beat instead of
                # starting from chapter 1.

                # Step 1: Initialize genre configuration
                active_genre_path = paths.get_active_genre_path()
                # Only (re)build genre when missing or --from-scratch. Passing
                # --genre on a normal resume used to force a regen and clobber
                # an existing active_genre.json (chapter count / prompts).
                should_init_genre = args.from_scratch or not active_genre_path.exists()
                if should_init_genre and args.genre:
                    banner("STEP 1: Initializing genre configuration")
                    cmd = [sys.executable, str(root_dir / "foundation" / "gen_genre_framework.py")]
                    if args.genre:
                        cmd += ["--genre", args.genre]
                    if args.chapters:
                        cmd += ["--chapters", args.chapters]
                    if args.words_per_chapter:
                        cmd += ["--words-per-chapter", str(args.words_per_chapter)]
                    if notes_for_genre:
                        cmd += ["--notes", notes_for_genre]
                    if args.perspective:
                        cmd += ["--perspective", args.perspective]
                    if getattr(args, "prose_mode", ""):
                        cmd += ["--prose-mode", args.prose_mode]
                    subprocess.run(cmd, check=True, timeout=900)
                    from core.genre import reload_genre
                    reload_genre()
                    # Genre is the source of truth for chapter count once written.
                    # Sync immediately — the startup sync already ran (and no-op'd
                    # if this file did not exist yet).
                    try:
                        genre_cfg = load_genre()
                        genre_total = int(
                            genre_cfg["generation"]["outline"]["estimated_chapters"]
                        )
                        if genre_total and genre_total != state.get("chapters_total"):
                            state["chapters_total"] = genre_total
                            save_state(state)
                            print(f"  chapters_total synced from genre config → {genre_total}")
                    except (FileNotFoundError, KeyError, TypeError, ValueError) as e:
                        print(f"  WARN: could not sync chapters_total from genre: {e}",
                              file=sys.stderr)
                    print("Genre config ready.\n")

                state = run_foundation(state)
            elif phase == "drafting":
                state = run_drafting(state)
            elif phase == "revision":
                state = run_revision(
                    state,
                    max_cycles=revision_cycles,
                    skip_adversarial_editing=args.skip_adversarial_editing,
                    skip_mechanical_cuts=args.skip_mechanical_cuts,
                    skip_reader_panel=args.skip_reader_panel,
                    skip_targeted_revisions=args.skip_targeted_revisions,
                    skip_full_novel_eval=args.skip_full_novel_eval,
                    skip_opus_review=args.skip_opus_review
                )
            elif phase == "export":
                state = run_export(state)
            else:
                print(f"Unknown phase: {phase}")
                sys.exit(1)
        except KeyboardInterrupt:
            banner("INTERRUPTED — state saved")
            save_state(state)
            sys.exit(130)
        except Exception as e:
            print(f"\n  FATAL ERROR in {phase}: {e}")
            save_state(state)
            raise

    elapsed = datetime.now() - start_time
    hours = elapsed.total_seconds() / 3600

    # Update project registry with final metadata
    update_registry(args.project, {
        "title": state.get("title", args.project),
        "genre": args.genre or os.getenv("GESAKU_GENRE", "unknown"),
        "created_at": state.get("created_at", datetime.now().isoformat()),
        "last_modified": datetime.now().isoformat(),
        "phase": state.get("phase", "unknown"),
        "novel_score": state.get("novel_score"),
        "word_count": count_words_in_chapters(),
    })

    banner("PIPELINE COMPLETE")
    print(f"  Project:    {args.project}")
    print(f"  Time:       {hours:.1f} hours")
    print(f"  Phase:      {state.get('phase')}")
    print(f"  Foundation: {state.get('foundation_score', 0)}")
    print(f"  Chapters:   {state.get('chapters_drafted', 0)}/{state.get('chapters_total', '?')}")
    print(f"  Words:      {count_words_in_chapters()}")
    print(f"  Novel:      {fmt_score(state.get('novel_score'))}")
    print(f"  Cycles:     {state.get('revision_cycle', 0)}")

    # Restore stdout/stderr and close the log file
    sys.stdout = sys.stdout.original
    sys.stderr = sys.stderr.original
    log_fh.close()


def main():
    parser = argparse.ArgumentParser(
        description="Gesaku pipeline orchestrator — seed to finished novel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python run_pipeline.py --project mynovel              # resume from current state
  python run_pipeline.py --project mynovel --from-scratch  # start fresh from seed.txt
  python run_pipeline.py --project mynovel --phase foundation  # run only foundation
  python run_pipeline.py --project mynovel --phase drafting    # run only drafting
  python run_pipeline.py --project mynovel --phase revision    # run only revision
  python run_pipeline.py --project mynovel --phase export      # run only export
  python run_pipeline.py --project mynovel --max-cycles 4      # limit revision to 4 cycles
""")

    parser.add_argument(
        "--project", default=os.environ.get("GESAKU_PROJECT", "default"),
        help="Project name (creates isolated session in projects/<name>/)")
    parser.add_argument(
        "--from-scratch", action="store_true",
        help="Reset state and start from seed.txt")
    parser.add_argument(
        "--phase", choices=PHASE_ORDER,
        help="Run only a specific phase")
    parser.add_argument(
        "--max-cycles", type=int, default=None,
        help=f"Maximum revision cycles (deprecated synonym for --revision-cycles)")
    parser.add_argument(
        "--revision-cycles", type=int, default=6,
        help="Number of revision cycles (default: 6)")
    parser.add_argument(
        "--skip-adversarial-editing", action="store_true",
        help="Skip adversarial editing phase inside revision cycle")
    parser.add_argument(
        "--skip-mechanical-cuts", action="store_true",
        help="Skip mechanical cuts phase inside revision cycle")
    parser.add_argument(
        "--skip-reader-panel", action="store_true",
        help="Skip reader panel phase inside revision cycle")
    parser.add_argument(
        "--skip-targeted-revisions", action="store_true",
        help="Skip targeted revisions phase inside revision cycle")
    parser.add_argument(
        "--skip-full-novel-eval", action="store_true",
        help="Skip full novel evaluation phase inside revision cycle")
    parser.add_argument(
        "--skip-opus-review", action="store_true",
        help="Skip Opus review loop phase")
    parser.add_argument(
        "--perspective", default=os.environ.get("GESAKU_PERSPECTIVE", ""),
        choices=["", "first_person", "third_person"],
        help="Force narrative perspective (first_person / third_person). "
             "Empty = foundation decides.")
    parser.add_argument(
        "--prose-mode", dest="prose_mode",
        default=os.environ.get("GESAKU_PROSE_MODE", ""),
        choices=["", "first_intimate", "first_voicey", "third_close", "third_scene"],
        help="Prose distance pack (fuel/prose/*.md). Empty = no pack.")
    parser.add_argument("--genre", default=os.environ.get("GESAKU_GENRE", ""),
                        help="Genre description (e.g., 'Cyberpunk Noir')")
    parser.add_argument("--chapters", default=os.environ.get("GESAKU_CHAPTERS", "24"),
                        help="Number of chapters (or 'short story', 'novella', etc.)")
    parser.add_argument("--words-per-chapter", type=int, default=3200,
                        help="Target word count per chapter (default: 3200)")
    parser.add_argument("--notes", default=os.environ.get("GESAKU_NOTES", ""),
                        help="Story premise or file path (e.g., --notes my_ideas.txt). "
                             "Auto-expands if <300 words, auto-summarizes if >1500.")

    args = parser.parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
