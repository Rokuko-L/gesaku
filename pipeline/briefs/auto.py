"""Auto brief: pick the weakest chapter and combine the sources."""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import subprocess
import sys
from pathlib import Path

import re

from core import paths
from pipeline.briefs.context import (
    chapter_text, chapter_title, extract_voice_rules, latest_chapter_eval,
    latest_full_eval, load_cuts, load_json, load_panel,
    panel_mentions_for_chapter, word_count,
)
from pipeline.briefs.eval import build_eval_brief
from pipeline.briefs.panel import build_panel_brief


def build_auto_brief(extra_rules: list[str] | None = None) -> tuple[int, str]:
    """Auto-detect weakest chapter and build a combined brief."""
    full_eval_path = latest_full_eval()
    if full_eval_path is None:
        print("No *_full.json found in eval_logs/. Running evaluate.py --full first...", file=sys.stderr)
        import subprocess
        subprocess.run([sys.executable, "pipeline/evaluate.py", "--full"], check=True)
        full_eval_path = latest_full_eval()
        if full_eval_path is None:
            raise FileNotFoundError("no *_full.json found in eval_logs/ even after running evaluate.py --full")

    full_eval = load_json(full_eval_path)
    ch = full_eval.get("weakest_chapter")
    if ch is None:
        raise ValueError("full eval does not contain 'weakest_chapter'")

    print(f"Auto-detected weakest chapter: {ch}", file=sys.stderr)
    print(f"  Source: {full_eval_path.name}", file=sys.stderr)

    top_sug = full_eval.get("top_suggestion", "")
    weakest_dim = full_eval.get("weakest_dimension", "")
    novel_score = full_eval.get("novel_score", "?")

    text = chapter_text(ch)
    title = chapter_title(text)
    wc = word_count(text)
    voice_rules = extract_voice_rules() + (extra_rules or [])

    problem_parts: list[str] = []
    keep_parts: list[str] = []
    change_parts: list[str] = []
    change_num = 1

    # Full eval context
    problem_parts.append(
        f"**Weakest chapter in the novel** (novel score: {novel_score}/10, "
        f"weakest dimension: {weakest_dim})."
    )
    if top_sug:
        problem_parts.append(f"**Top suggestion from full eval:** {top_sug}")

    # Per-dimension notes from full eval that mention this chapter
    dim_keys = [
        "arc_completion", "pacing_curve", "theme_coherence",
        "foreshadowing_resolution", "world_consistency", "voice_consistency",
        "overall_engagement",
    ]
    ch_re = re.compile(rf"\b(?:Chapters?|Ch\.?)\s*{ch}\b", re.I)
    for dk in dim_keys:
        dim = full_eval.get(dk, {})
        note = dim.get("note", "")
        if ch_re.search(note):
            score = dim.get("score", "?")
            problem_parts.append(f"**{dk.replace('_', ' ').title()}** ({score}/10): {note}")

    # Per-chapter eval
    ch_eval_path = latest_chapter_eval(ch)
    if ch_eval_path:
        ch_eval = load_json(ch_eval_path)
        overall = ch_eval.get("overall_score", "?")
        problem_parts.append(f"\nPer-chapter eval score: **{overall}/10**")

        # Weakest moments
        for dk in ["voice_adherence", "beat_coverage", "character_voice",
                    "plants_seeded", "prose_quality", "engagement"]:
            dim = ch_eval.get(dk)
            if not dim or not isinstance(dim, dict):
                continue
            score = dim.get("score", "?")
            fix = dim.get("fix", "")
            if score != "?" and int(score) <= 7 and fix:
                change_parts.append(f"{change_num}. [{dk}] {fix}")
                change_num += 1

        # Top 3 revisions
        for rev in ch_eval.get("top_3_revisions", []):
            change_parts.append(f"{change_num}. {rev}")
            change_num += 1

        # AI patterns
        ai_patterns = ch_eval.get("ai_patterns_detected", [])
        if ai_patterns:
            problem_parts.append("\n**AI patterns detected:**")
            for pat in ai_patterns:
                problem_parts.append(f"- {pat}")

        # Strongest sentences
        strongest = ch_eval.get("three_strongest_sentences", [])
        if strongest:
            keep_parts.append("Strongest sentences (eval):")
            for s in strongest:
                keep_parts.append(f'- "{s}"')

        # Weakest sentences
        weakest_sents = ch_eval.get("three_weakest_sentences", [])
        if weakest_sents:
            problem_parts.append("\n**Weakest sentences:**")
            for s in weakest_sents:
                problem_parts.append(f'- "{s}"')

    # Panel cross-reference
    panel = load_panel()
    if panel:
        info = panel_mentions_for_chapter(panel, ch)
        mentions = info["mentions"]
        flagged = info["flagged_issues"]
        if flagged:
            problem_parts.append("\n**Panel flags:**")
            for f in flagged:
                problem_parts.append(f"- {f}")

        for key in ["worst_scene", "momentum_loss", "cut_candidate"]:
            if mentions[key]:
                problem_parts.append(f"\n**Panel — {key.replace('_', ' ')}:**")
                for m in mentions[key]:
                    snippet = m[:400] + "..." if len(m) > 400 else m
                    problem_parts.append(snippet)

        if mentions["best_scene"]:
            for m in mentions["best_scene"]:
                snippet = m[:400] + "..." if len(m) > 400 else m
                keep_parts.append(f"Panel best scene mention: {snippet}")

    # Cuts data
    cuts_data = load_cuts(ch)
    if cuts_data:
        total_cuttable = cuts_data.get("total_cuttable_words", 0)
        fat_pct = cuts_data.get("overall_fat_percentage", 0)
        tightest = cuts_data.get("tightest_passage", "")
        verdict = cuts_data.get("one_sentence_verdict", "")

        if total_cuttable:
            problem_parts.append(
                f"\n**Adversarial edit:** {total_cuttable} cuttable words ({fat_pct}% fat). "
                f"{verdict}"
            )
        if tightest:
            keep_parts.append(f'\nTightest passage (adversarial edit):\n> {tightest}')

        # Add top cuts as change items
        cuts_list = cuts_data.get("cuts", [])
        # Only include the most impactful — REDUNDANT and OVER-EXPLAIN
        priority_cuts = [c for c in cuts_list if c.get("type") in ("REDUNDANT", "OVER-EXPLAIN")]
        for c in priority_cuts[:5]:
            quote = c.get("quote", "")[:150]
            reason = c.get("reason", "")
            action = c.get("action", "CUT")
            rewrite = c.get("rewrite")
            entry = f'{change_num}. `"{quote}..."` — {reason}'
            if action == "REWRITE" and rewrite:
                entry += f'\n   → Rewrite as: "{rewrite}"'
            elif action == "CUT":
                entry += "\n   → Cut entirely"
            change_parts.append(entry)
            change_num += 1

    # Top suggestion from full eval as final change item
    if top_sug:
        change_parts.append(f"{change_num}. [PRIORITY — full eval] {top_sug}")
        change_num += 1

    if not keep_parts:
        keep_parts.append("(Review chapter for strongest passages before revising.)")
    if not change_parts:
        change_parts.append("(No specific changes auto-detected. Manual review recommended.)")

    # Determine brief type
    brief_type = "AUTO-FIX"

    target_note = f"~{wc} words (current: {wc}; adjust based on revision scope)"

    brief = f"# Revision Brief: Chapter {ch} — {title} ({brief_type})\n\n"
    brief += "## PROBLEM\n"
    brief += "\n".join(problem_parts) + "\n\n"
    brief += "## WHAT TO KEEP\n"
    brief += "\n".join(keep_parts) + "\n\n"
    brief += "## WHAT TO CHANGE\n"
    brief += "\n".join(change_parts) + "\n\n"
    brief += "## VOICE RULES\n"
    brief += "\n".join(f"- {r}" for r in voice_rules) + "\n\n"
    brief += "## TARGET\n"
    brief += target_note + "\n"

    return ch, brief


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
