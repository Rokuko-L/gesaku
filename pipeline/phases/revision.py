"""Phase 3 — Revision: adversarial edits, reader panel, targeted rewrites.

Cycles keep edits that pass tolerance, so the last cycle is not necessarily
the best one — the peak score/commit is tracked for export.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core import paths
from pipeline.review import should_stop as review_should_stop

from pipeline.pipeline_infra import (
    banner, best_novel_checkpoint, chapter_threshold, count_chapter_files,
    count_words_in_chapters, cuts_tolerance, decline_streak, fmt_score,
    get_historical_best_for_chapter, git_add_commit, git_commit_staged,
    git_reset_hard, git_short_hash, log_result, max_revision_cycles,
    min_revision_cycles, near_clean_margin, parse_score, parse_score_any,
    plateau_delta, record_novel_score, resolve_chapters_total,
    revision_tolerance, run_tool, save_state, step, store_novel_score,
    timeout_for, uv_run,
)
from pipeline.phases.common import (
    on_chapter_kept, resync_canon_after_cycle,
)
from pipeline.phases.review_loop import run_opus_review_loop



# ---------------------------------------------------------------------------
# PHASE 3 — REVISION
# ---------------------------------------------------------------------------

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
    tolerance = revision_tolerance()

    for cycle in range(start_cycle, max_cycles + 1):
        banner(f"Revision Cycle {cycle}/{max_cycles}", "-")

        # Check if we should run adversarial editing or mechanical cuts
        run_adv = not skip_adversarial_editing
        apply_cuts = paths.get_root_dir() / "apply_cuts.py"
        run_cuts = not skip_mechanical_cuts and apply_cuts.exists()

        if run_adv or run_cuts:
            # Evaluate current baseline score (before Step 1/2 edits)
            step("Evaluating baseline novel score before Cycle edits...")
            baseline_eval = uv_run("pipeline/evaluate.py --full", timeout=timeout_for("long"))
            cycle_baseline_score = parse_score(baseline_eval.stdout, "novel_score")
            if cycle_baseline_score < 0:
                cycle_baseline_score = parse_score(baseline_eval.stdout, "overall_score")

            post_adv_score = cycle_baseline_score
            if run_adv:
                # -- Step 1: Adversarial editing pass (parallel per chapter) --
                step("Running adversarial editing on all chapters...")
                total_ch = resolve_chapters_total(state)
                # Parallelism: default 4 workers even for local proxies —
                # build_arc_summary/build_outline already run 4-12 concurrent
                # LLM calls against the same endpoint. Override with
                # GESAKU_MAX_WORKERS (e.g. =1 for weak single-request models).
                max_workers = int(os.getenv("GESAKU_MAX_WORKERS", "4"))
                adv_timeout = timeout_for("standard")
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {
                        pool.submit(uv_run, f"adversarial_edit.py {ch}", adv_timeout): ch
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
                post_adv_eval = uv_run("pipeline/evaluate.py --full", timeout=timeout_for("long"))
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
                         "--types OVER-EXPLAIN REDUNDANT --min-fat 15", timeout=timeout_for("short"))

                # Evaluate full novel score after Step 2
                step("Evaluating novel score after Mechanical Cuts...")
                post_cuts_eval = uv_run("pipeline/evaluate.py --full", timeout=timeout_for("long"))
                post_cuts_score = parse_score_any(post_cuts_eval.stdout, "novel_score", "overall_score")

                step(f"Mechanical cuts score shift: {post_adv_score} -> {post_cuts_score}")
                
                if post_cuts_score >= (post_adv_score - cuts_tolerance()):
                    run_tool("git add -A", cwd=str(paths.get_project_dir()))
                    commit_hash = git_add_commit(
                        f"revision cycle {cycle}: apply mechanical cuts {post_adv_score}->{post_cuts_score}"
                    )
                    log_result(commit_hash, f"rev-cycle-{cycle}-cuts", post_cuts_score,
                               count_words_in_chapters(), "keep",
                               f"Cycle {cycle}: Step 2 mechanical cuts kept {post_adv_score}->{post_cuts_score}")
                    post_cuts_commit = commit_hash
                    record_novel_score(state, post_cuts_score, post_cuts_commit)
                else:
                    step(f"Mechanical cuts made the novel worse ({post_cuts_score} < {post_adv_score - cuts_tolerance()}), reverting cuts")
                    git_reset_hard("HEAD")
                    log_result("reverted", f"rev-cycle-{cycle}-cuts", post_cuts_score,
                               count_words_in_chapters(), "discard",
                               f"Cycle {cycle}: Step 2 mechanical cuts regressed {post_adv_score}->{post_cuts_score}")
                    record_novel_score(state, post_adv_score, git_short_hash())
            else:
                if skip_mechanical_cuts:
                    step("Skipping mechanical cuts as requested")
                else:
                    step("apply_cuts.py not found, skipping mechanical cuts")
                record_novel_score(state, post_adv_score, git_short_hash())
        else:
            step("Skipping both adversarial editing and mechanical cuts — no Cycle edits to apply")

        # -- Step 3: Generate arc summary + Reader panel --
        if not skip_reader_panel:
            step("Generating arc summary for reader panel...")
            n_ch_arc = count_chapter_files()
            arc_timeout = max(timeout_for("short"),
                              -(-n_ch_arc // 4) * timeout_for("short") // 2 + 60)
            uv_run("pipeline/build_arc_summary.py", timeout=arc_timeout)
            step("Running reader panel evaluation...")
            uv_run("pipeline/reader_panel.py", timeout=timeout_for("long"))
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
                    T_STD = timeout_for("standard")
                    T_SHORT = timeout_for("short")
                    pre_eval = uv_run(f"pipeline/evaluate.py --chapter={ch_num}", timeout=T_STD)
                    pre_score = parse_score(pre_eval.stdout, "overall_score")

                    brief_file = briefs_dir / f"ch{ch_num:02d}_cycle{cycle}_{question}.md"
                    gen_brief_py = paths.get_root_dir() / "pipeline" / "gen_brief.py"
                    if gen_brief_py.exists():
                        run_tool(f"uv run python pipeline/gen_brief.py --panel {ch_num}", timeout=T_SHORT)
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
                    uv_run(f'pipeline/gen_revision.py {ch_num} "{brief_file}"', timeout=T_STD)

                    post_eval = uv_run(f"pipeline/evaluate.py --chapter={ch_num}", timeout=T_STD)
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
            T_LONG = timeout_for("long")
            full_eval = uv_run("pipeline/evaluate.py --full", timeout=T_LONG)
            try:
                novel_score = parse_score_any(full_eval.stdout, "novel_score", "overall_score")
            except ValueError as e:
                step(f"WARNING: could not parse any score from full eval — keeping previous score. {e}")
                novel_score = prev_score

            if novel_score is None or novel_score <= 0.0:
                step("Novel score missing/0.0 detected, retrying evaluation...")
                retry_eval = uv_run("pipeline/evaluate.py --full", timeout=T_LONG)
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

        stored = record_novel_score(state, novel_score, None)
        state["revision_cycle"] = cycle
        save_state(state)

        # -- Step 7: Plateau detection --
        if not skip_full_novel_eval:
            plateau = plateau_delta()
            min_cycles = min_revision_cycles()

            # Declining-score early stop. The plateau check below only fires
            # on a STABLE score (|delta| < plateau), so a cycle sequence that
            # keeps dropping never trips it and the loop burns cycles to
            # max_cycles. Each cycle is hours of LLM time, so bail out once
            # the score has fallen for N consecutive cycles.
            if stored is not None and prev_score is not None:
                if stored < prev_score:
                    state["revision_decline_streak"] = (
                        state.get("revision_decline_streak", 0) + 1)
                else:
                    state["revision_decline_streak"] = 0
                save_state(state)
                streak = state["revision_decline_streak"]
                if cycle >= min_cycles and streak >= decline_streak():
                    step(f"Declining for {streak} consecutive cycles "
                         f"({fmt_score(prev_score)} -> {fmt_score(stored)}); "
                         f"best remains {fmt_score(state.get('best_novel_score'))} "
                         f"@ {state.get('best_novel_commit')} — stopping revision "
                         f"(export ships the best checkpoint)")
                    break

            comparable = (
                cycle >= min_cycles
                and stored is not None
                and prev_score is not None
                and abs(stored - prev_score) < plateau
            )
            if comparable:
                # Secondary gate: don't stop while >30% of chapters are below threshold
                total_ch = resolve_chapters_total(state)
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

    run_opus_review_loop(skip=skip_opus_review)

    state["phase"] = "export"
    state["current_focus"] = "export"
    save_state(state)

    banner(f"REVISION COMPLETE — {state.get('revision_cycle', 0)} cycles, "
           f"novel_score {fmt_score(state.get('novel_score'))}")
    return state
