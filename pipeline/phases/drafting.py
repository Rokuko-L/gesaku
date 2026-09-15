"""Phase 2 — Drafting: sequential chapters with eval + retry.

A judge failure is not a fatal error here: an attempt whose eval cannot be
parsed is logged `unevaluated` and drafting continues.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from core import paths
from core.genre import load_genre
from core.outline import extract_outline_debts

from pipeline.pipeline_infra import (
    INFRA_MAX_ATTEMPTS, banner, chapter_threshold, count_words_in_chapters,
    force_keep_margin, get_total_chapters, git_add_commit, log_result,
    max_chapter_attempts, near_clean_margin, parse_score,
    resolve_chapters_total, run_tool, save_state, step, store_novel_score,
    timeout_for, uv_run,
)
from pipeline.phases.common import (
    build_eval_feedback, on_chapter_kept, resync_canon_after_cycle,
    update_canon_from_eval,
)



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
        result = uv_run("pipeline/retrofit_reveal.py", timeout=timeout_for("long"))
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

    total = resolve_chapters_total(state)
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
    force_keep_floor = chapter_gate - force_keep_margin()

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
                    fb_path = paths.get_retry_feedback_path(ch)
                    fb_path.write_text(retry_feedback, encoding="utf-8")
                    cmd = (f"\"{sys.executable}\" pipeline/draft_chapter.py {ch} "
                           f"--retry-feedback \"{fb_path}\"")
                draft_result = run_tool(cmd, timeout=timeout_for("standard"), check=False)
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

            # Evaluate. A dead judge must not kill a good draft: retry the
            # eval, and if it still fails treat the attempt as unscored
            # (warning + continue) rather than a fatal drafting error.
            eval_result = None
            score = None
            for eval_try in range(1, 4):
                eval_result = uv_run(f"pipeline/evaluate.py --chapter={ch}", timeout=timeout_for("long"))
                try:
                    score = parse_score(eval_result.stdout, "overall_score")
                    break
                except ValueError as e:
                    if eval_try < 3:
                        step(f"eval parse failed for Ch {ch} (try {eval_try}/3): {e} — retrying eval")
                        time.sleep(5 * eval_try)
                        continue
                    step(f"WARNING: eval failed 3x for Ch {ch} — keeping draft unscored, "
                         f"judge output unusable ({e}). Attempt discarded, drafting continues.")
                    log_result("unevaluated", f"ch{ch:02d}", 0, word_count,
                               "discard", f"Chapter {ch} attempt {attempt}: eval judge failed")
                    score = None
                    break
            if score is None:
                continue
            step(f"Chapter {ch} score: {score}")

            # Pin the exact eval log of THIS attempt (evaluate.py prints 'eval_log: <path>')
            eval_log_path = None
            log_m = re.search(r"eval_log:\s*(\S+)", eval_result.stdout)
            if log_m:
                eval_log_path = Path(log_m.group(1))
                attempt_log_paths[attempt] = eval_log_path

            if score >= chapter_gate:
                fb_path = paths.get_retry_feedback_path(ch)
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
                            timeout=timeout_for("standard"), check=False)
                        if rep.returncode == 0:
                            rep_wc = len(ch_file.read_text(encoding="utf-8").split())
                            step(f"Repaired Ch {ch} ({rep_wc}w) — re-evaluating...")
                            rep_eval = uv_run(f"pipeline/evaluate.py --chapter={ch}", timeout=timeout_for("standard"))
                            try:
                                rep_score = parse_score(rep_eval.stdout, "overall_score")
                            except ValueError as e:
                                step(f"WARNING: repair re-eval unparseable for Ch {ch} ({e}) — "
                                     f"keeping pre-repair draft as fallback")
                                rep_score = score
                            step(f"Repaired Ch {ch} score: {rep_score}")
                            if rep_score >= chapter_gate:
                                step(f"Repair lifted Ch {ch} over the bar — keeping")
                                fb_path = paths.get_retry_feedback_path(ch)
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
                paths.get_retry_feedback_path(ch).unlink(missing_ok=True)
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
