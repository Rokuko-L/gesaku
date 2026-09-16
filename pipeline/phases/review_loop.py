"""Phase 3b — Opus review loop (deep, prose-level refinement).

Split out of the revision phase: this pass is optional, non-blocking, and
has a different failure policy (warn and continue) from the scoring loop.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import json
import re
import sys
from pathlib import Path

from core import paths
from pipeline.review import should_stop as review_should_stop

from pipeline.pipeline_infra import (
    banner, cuts_tolerance, get_historical_best_for_chapter, git_add_commit,
    git_commit_staged, git_reset_hard, log_result, parse_score,
    parse_score_any, revision_tolerance, run_tool, step, timeout_for, uv_run,
)
from pipeline.phases.common import on_chapter_kept, stage_chapter_with_callbacks



def run_opus_review_loop(skip: bool = False) -> None:
    """Phase 3b — deep prose review via Opus.

    Non-blocking by design: the novel is already written, so a failed critic
    pass warns and falls through to export rather than losing the work.
    """
    review_py = paths.get_root_dir() / "pipeline" / "review.py"
    if not skip and review_py.exists():
        banner("PHASE 3b: OPUS REVIEW LOOP", "=")

        max_review_rounds = 4
        for rnd in range(1, max_review_rounds + 1):
            banner(f"Opus Review Round {rnd}/{max_review_rounds}", "-")

            # Step 1: Generate the review
            step("Sending manuscript to Opus for review...")
            try:
                review_result = uv_run(
                    f'pipeline/review.py --output "{paths.get_reviews_path()}"',
                    timeout=timeout_for("standard"))
            except Exception as e:
                step(f"WARNING: Opus review round {rnd} failed ({e}) — skipping to export")
                break

            # Step 2: Parse the review
            step("Parsing review...")
            parse_result = run_tool(
                "uv run python pipeline/review.py --parse", timeout=timeout_for("short"))
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
                    run_tool("uv run python pipeline/gen_brief.py --auto", timeout=timeout_for("short"))
                
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
                        pre_eval = uv_run(f"pipeline/evaluate.py --chapter={ch_num}", timeout=timeout_for("standard"))
                        pre_score = parse_score(pre_eval.stdout, "overall_score")

                        step(f"Revising Ch {ch_num} from review brief...")
                        uv_run(f'pipeline/gen_revision.py {ch_num} "{brief}"', timeout=timeout_for("standard"))

                        # Evaluate post-revision score
                        step(f"Evaluating Ch {ch_num} after revision...")
                        post_eval = uv_run(f"pipeline/evaluate.py --chapter={ch_num}", timeout=timeout_for("standard"))
                        post_score = parse_score(post_eval.stdout, "overall_score")
                        
                        # Compare against historical best
                        hist_best_score, hist_best_commit = get_historical_best_for_chapter(ch_num)
                        baseline = max(pre_score, hist_best_score)
                        
                        step(f"Ch {ch_num} Review Revision: {pre_score} -> {post_score} (Historical best: {hist_best_score}, Baseline: {baseline})")
                        
                        ch_file = paths.get_chapters_dir() / f"ch_{ch_num:02d}.md"
                        word_count = len(ch_file.read_text(encoding="utf-8").split()) if ch_file.exists() else 0
                        
                        if post_score >= (baseline - revision_tolerance()):
                            # Same coupling as the revision phase: re-extract
                            # first so the plant store rides with the prose it
                            # describes.
                            on_chapter_kept(ch_num, reextract=True)
                            stage_chapter_with_callbacks(ch_num)
                            commit_hash = git_commit_staged(
                                f"review round {rnd}: revise ch{ch_num:02d} from Opus feedback {pre_score}->{post_score}")
                            log_result(commit_hash, f"rev-ch{ch_num:02d}-review", post_score,
                                       word_count, "keep",
                                       f"Round {rnd}: {brief.name} score {pre_score}->{post_score}")
                        else:
                            step(f"Review revision made it worse ({post_score} < {baseline - revision_tolerance()}). Reverting ch_{ch_num:02d} to best commit: {hist_best_commit}")
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
                            # The revert target is the best-scoring commit,
                            # which may be older than the prose the store was
                            # last extracted from — rebuild it from disk.
                            # Not staged: no commit happens on this path, and
                            # staging an uncommitted store leaves it open to a
                            # later `git reset --hard` restoring a stale copy.
                            on_chapter_kept(ch_num, reextract=True)
                            log_result("reverted", f"rev-ch{ch_num:02d}-review", post_score,
                                       word_count, "discard",
                                       f"Round {rnd}: {brief.name} regressed {pre_score}->{post_score}")
            
            # Step 5: Mechanical fixes from review
            # Run slop pass on any mentioned patterns
            step("Running mechanical cleanup pass...")
            apply_cuts_py = paths.get_root_dir() / "pipeline" / "apply_cuts.py"
            if apply_cuts_py.exists():
                # Evaluate score before cuts
                pre_cuts_eval = uv_run("pipeline/evaluate.py --full", timeout=timeout_for("long"))
                pre_cuts_score = parse_score_any(pre_cuts_eval.stdout, "novel_score", "overall_score")

                run_tool(
                    "uv run python pipeline/apply_cuts.py all --types OVER-EXPLAIN REDUNDANT --min-fat 15",
                    timeout=timeout_for("short"))

                # Evaluate score after cuts
                post_cuts_eval = uv_run("pipeline/evaluate.py --full", timeout=timeout_for("long"))
                post_cuts_score = parse_score_any(post_cuts_eval.stdout, "novel_score", "overall_score")

                step(f"Mechanical cuts score shift: {pre_cuts_score} -> {post_cuts_score}")

                if post_cuts_score >= (pre_cuts_score - cuts_tolerance()):
                    run_tool("git add -A", cwd=str(paths.get_project_dir()))
                    git_add_commit(f"review round {rnd}: mechanical cleanup {pre_cuts_score}->{post_cuts_score}")
                else:
                    step(f"Mechanical cuts made the novel worse ({post_cuts_score} < {pre_cuts_score - cuts_tolerance()}), reverting cuts")
                    git_reset_hard("HEAD")
            
            step(f"Review round {rnd} complete.")
        
        banner("OPUS REVIEW LOOP COMPLETE")
    elif skip:
        step("Skipping Opus review loop as requested")
    else:
        step("review.py not found, skipping Opus review loop")
