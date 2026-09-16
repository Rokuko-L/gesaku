"""Phase 1 — Foundation: planning documents + iteration loop.

Checkpointed per artifact: a step whose output already exists and passes a
cheap validity check is skipped, so a crash resumes at the missing artifact
instead of regenerating world/characters/canon/outline.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import json
import re
import sys

from core import paths
import os

from core.genre import load_genre
from core.llm import llm_timeout
from core.outline import (
    extract_outline_debts, validate_plants_harvests, validate_premise_beats,
)

from pipeline.pipeline_infra import (
    MAX_FOUNDATION_ITERS, MAX_OUTLINE_ATTEMPTS, FOUNDATION_PLATEAU_ITERS,
    banner, foundation_plateau, foundation_threshold, git_add_commit,
    git_reset_hard, load_state, log_result, parse_lore_score, parse_score,
    resolve_chapters_total, save_state, step, timeout_for, uv_run,
)


def _outline_subprocess_cap(state: dict) -> int:
    """Subprocess cap for gen_outline, derived from what it may actually do.

    A flat cap can expire mid-retry and abort the phase even though every
    individual LLM call stayed inside its own budget: the roadmap retries up
    to GESAKU_OUTLINE_ROADMAP_ATTEMPTS times and each block retries 3 times,
    all at `llm_timeout("long")`. `timeout_for("xlong")` alone (3600s) is
    less than the roadmap's own worst case (6 x 900s).

    The outer cap is only a backstop — a genuinely hung call is bounded by
    the per-call LLM timeout, so a generous ceiling here does not mean a
    hang goes undetected for that long.
    """
    from pipeline.pipeline_infra import _env_int
    roadmap_attempts = _env_int("GESAKU_OUTLINE_ROADMAP_ATTEMPTS", 6)
    block_size = _env_int("GESAKU_OUTLINE_BLOCK_SIZE", 4)
    block_attempts = 3  # matches gen_outline's per-block retry loop
    total = resolve_chapters_total(state)
    n_blocks = max(1, -(-total // max(block_size, 1)))
    return max(
        timeout_for("xlong"),
        (roadmap_attempts + n_blocks * block_attempts) * llm_timeout("long"),
    )



# ---------------------------------------------------------------------------
# PHASE 1 — FOUNDATION
# ---------------------------------------------------------------------------

def _foundation_artifact_ok(path, min_chars: int = 500,
                            require_chapters: int = 0) -> bool:
    """Cheap validity check for a foundation output file.

    Exists + non-trivial size (+ all chapter headers present when asked).
    Deeper validation (premise beats, hygiene) still runs below.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if len(text) < min_chars:
        return False
    if require_chapters:
        found = set(int(m) for m in re.findall(
            r'###\s*\*?\*?\s*Ch(?:apter)?\b\s*\*?\*?\s*(\d+)',
            text, re.IGNORECASE))
        if len(found) < require_chapters:
            return False
    return True


def _foundation_part2_ok(outline_path, total_ch: int) -> bool:
    """Part-2 polish checkpoint: done-marker present and tail chapters outlined.

    The marker (written by gen_outline_part2, cleared by gen_outline) is the
    only reliable signal — no prose heuristic can tell a part-1 outline from a
    part-2-polished one, so the old "FORESHADOW appears somewhere" test marked
    every iteration dirty and re-ran the whole refine pass.
    """
    try:
        text = outline_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not paths.get_outline_part2_path().exists():
        return False
    tail_start = max(1, total_ch - 3)
    heads = set(int(m) for m in re.findall(
        r'###\s*\*?\*?\s*Ch(?:apter)?\b\s*\*?\*?\s*(\d+)',
        text, re.IGNORECASE))
    return all(ch in heads for ch in range(tail_start, total_ch + 1))


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

        # Per-artifact checkpointing: skip steps whose outputs already exist
        # and pass a cheap validity check, so a crash no longer burns a full
        # regen of world/characters/canon/outline. --from-scratch wipes the
        # files, which is the explicit invalidation path.
        world_ok = _foundation_artifact_ok(
            paths.get_world_path(), min_chars=2000)
        chars_ok = _foundation_artifact_ok(
            paths.get_characters_path(), min_chars=1000)
        canon_path = paths.get_canon_path()
        outline_path = paths.get_outline_path()
        total_ch = resolve_chapters_total(state)
        outline_ok = _foundation_artifact_ok(
            outline_path, min_chars=2000 * total_ch // 4,
            require_chapters=total_ch)

        # 1. Generate planning documents (skipping valid checkpoints)
        FX = timeout_for("xlong")
        if world_ok:
            step("World bible exists — skipping regen (checkpoint)")
        else:
            step("Generating world bible...")
            uv_run("foundation/gen_world.py", timeout=FX)

        if chars_ok:
            step("Characters exist — skipping regen (checkpoint)")
        else:
            step("Generating characters...")
            uv_run("foundation/gen_characters.py", timeout=FX)

        step("Generating title tournament...")
        current_title = load_state().get("title", "")
        if not current_title or current_title == "Untitled":
            uv_run("foundation/gen_title.py", timeout=FX)
            try:
                from core.genre import reload_genre
                reload_genre()
            except Exception:
                pass

        # Canon before outline so plant hygiene can use sealed-fact denylist.
        if _foundation_artifact_ok(canon_path, min_chars=500):
            step("Canon exists — skipping regen (checkpoint)")
        else:
            step("Generating canon...")
            uv_run("foundation/gen_canon.py", timeout=FX)

        if outline_ok:
            step("Outline exists — skipping regen (checkpoint)")
        else:
            step("Generating outline (part 1)...")
            uv_run("foundation/gen_outline.py", timeout=_outline_subprocess_cap(state))

        # Validate Chapter 1 premise beats (pre-draft gate)
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
            uv_run(f'foundation/gen_outline.py --retry-feedback "{error}.{format_hint}"', timeout=timeout_for("standard"))

        # Write premise validation sidecar
        prem_val_path = paths.get_premise_validation_path()
        paths.save_json_atomic({
            "passed": premise_passed,
            "attempts": oa if premise_passed else MAX_OUTLINE_ATTEMPTS,
            "last_error": "" if premise_passed else premise_last_error,
        }, prem_val_path)

        part2_ok = _foundation_part2_ok(outline_path, total_ch)
        if outline_ok and part2_ok:
            step("Outline part 2 exists — skipping regen (checkpoint)")
        else:
            step("Generating outline (part 2 — foreshadowing)...")
            n_blocks = max(1, -(-resolve_chapters_total(state) // 10))
            uv_run("foundation/gen_outline_part2.py",
                   timeout=max(timeout_for("standard"),
                               n_blocks * llm_timeout("standard")))

        step("Sanitizing chapter titles...")
        uv_run("pipeline/sanitize_outline_titles.py", timeout=timeout_for("short"))

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
            paths.get_plant_hygiene_path().write_text(
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
        # load_state() can carry a stale value on disk (subprocess writes only
        # touch their own keys); re-assert the iteration we are actually on so
        # a crash resumes at i+1 instead of re-running from 1.
        state["iteration"] = i
        debts = extract_outline_debts(outline_text)
        state["debts"] = debts
        save_state(state)
        step(f"Logged {len(debts)} active narrative debts in project state.")

        step("Running voice fingerprint...")
        uv_run("pipeline/voice_fingerprint.py", timeout=timeout_for("standard"))

        # 2. Evaluate
        step("Evaluating foundation...")
        eval_result = uv_run("pipeline/evaluate.py --phase=foundation", timeout=timeout_for("standard"))
        try:
            score = parse_score(eval_result.stdout, "overall_score")
            lore = parse_lore_score(eval_result.stdout)
        except ValueError as e:
            # A judge that omits a key is not fatal: count the iteration as
            # non-improving (score 0) so the plateau exit fires instead of
            # aborting the phase after an expensive iteration.
            step(f"WARNING: foundation judge output unparseable ({e}) — "
                 f"counting as a non-improving iteration")
            score, lore = 0.0, 0.0

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

    # Chapter count has one owner: the genre config. Resolve (syncing
    # state) instead of stamping a stale state value.
    total = resolve_chapters_total(state)
    state["phase"] = "drafting"
    state["current_focus"] = "chapter_drafting"
    save_state(state)

    banner(f"FOUNDATION COMPLETE — score {best_score}, {total} chapters planned")
    return state

