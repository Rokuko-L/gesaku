"""Revision brief from chapter/full eval callouts."""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import re

from core import paths
from pipeline.briefs.context import (
    chapter_text, chapter_title, extract_voice_rules, load_cuts,
    latest_chapter_eval, latest_full_eval, load_json, word_count,
)


def build_eval_brief(ch: int, extra_rules: list[str] | None = None) -> str:
    # Try per-chapter eval first, fall back to full eval
    ch_eval_path = latest_chapter_eval(ch)
    full_eval_path = latest_full_eval()

    if ch_eval_path is None and full_eval_path is None:
        raise FileNotFoundError(f"no eval logs found for chapter {ch}")

    text = chapter_text(ch)
    title = chapter_title(text)
    wc = word_count(text)
    voice_rules = extract_voice_rules() + (extra_rules or [])

    problem_parts: list[str] = []
    keep_parts: list[str] = []
    change_parts: list[str] = []
    change_num = 1

    # Per-chapter eval data
    if ch_eval_path:
        ch_eval = load_json(ch_eval_path)

        # Overall score and weakest dimension
        overall = ch_eval.get("overall_score", "?")
        weakest_dim = ch_eval.get("weakest_dimension", "unknown")
        problem_parts.append(
            f"Per-chapter eval score: **{overall}/10**. "
            f"Weakest dimension: **{weakest_dim}**."
        )

        # Collect weakest moments from each dimension
        dim_keys = [
            "voice_adherence", "beat_coverage", "character_voice",
            "plants_seeded", "prose_quality", "continuity",
            "canon_compliance", "lore_integration", "engagement",
        ]
        for dk in dim_keys:
            dim = ch_eval.get(dk)
            if not dim or not isinstance(dim, dict):
                continue
            score = dim.get("score", "?")
            weakest = dim.get("weakest_moment", "")
            fix = dim.get("fix", "")
            if score != "?" and int(score) <= 7 and weakest:
                problem_parts.append(
                    f"**{dk.replace('_', ' ').title()}** ({score}/10): {weakest}"
                )
                if fix:
                    change_parts.append(f"{change_num}. [{dk}] {fix}")
                    change_num += 1

        # Top 3 revisions
        top_revs = ch_eval.get("top_3_revisions", [])
        for rev in top_revs:
            change_parts.append(f"{change_num}. {rev}")
            change_num += 1

        # AI patterns detected
        ai_patterns = ch_eval.get("ai_patterns_detected", [])
        if ai_patterns:
            problem_parts.append("**AI patterns detected:**")
            for pat in ai_patterns:
                problem_parts.append(f"- {pat}")

        # Strongest sentences
        strongest = ch_eval.get("three_strongest_sentences", [])
        if strongest:
            keep_parts.append("Strongest sentences (eval):")
            for s in strongest:
                keep_parts.append(f'- "{s}"')

        # Three weakest sentences for reference
        weakest_sents = ch_eval.get("three_weakest_sentences", [])
        if weakest_sents:
            problem_parts.append("**Weakest sentences:**")
            for s in weakest_sents:
                problem_parts.append(f'- "{s}"')

    # Full eval data — add context if this chapter is flagged
    if full_eval_path:
        full_eval = load_json(full_eval_path)
        weakest_ch = full_eval.get("weakest_chapter")
        top_sug = full_eval.get("top_suggestion", "")
        novel_score = full_eval.get("novel_score", "?")

        if weakest_ch == ch:
            problem_parts.insert(0,
                f"**This is the novel's weakest chapter** per full eval "
                f"(novel score: {novel_score}/10)."
            )
        if top_sug and (weakest_ch == ch or ch_eval_path is None):
            change_parts.append(
                f"{change_num}. [full eval top suggestion] {top_sug}"
            )
            change_num += 1

        # Pacing curve note if it mentions this chapter
        pacing = full_eval.get("pacing_curve", {})
        pacing_note = pacing.get("note", "")
        ch_re = re.compile(rf"\b(?:Chapter|Ch\.?)\s*{ch}\b", re.I)
        if ch_re.search(pacing_note):
            problem_parts.append(f"**Pacing note (full eval):** {pacing_note}")

    # Tightest passage from cuts
    cuts_data = load_cuts(ch)
    if cuts_data and cuts_data.get("tightest_passage"):
        keep_parts.append(
            f'Tightest passage (adversarial edit): "{cuts_data["tightest_passage"]}"'
        )

    if not keep_parts:
        keep_parts.append("(Review chapter for strongest passages before revising.)")

    if not change_parts:
        change_parts.append("(No specific revision items from eval. Check --panel or --cuts.)")

    # Determine type from eval
    if ch_eval_path:
        ch_eval = load_json(ch_eval_path)
        overall = ch_eval.get("overall_score", 10)
        if overall <= 5:
            brief_type = "REWRITE"
        elif overall <= 7:
            brief_type = "FIX"
        else:
            brief_type = "POLISH"
    else:
        brief_type = "FIX"

    target_note = f"~{wc} words (current length: {wc}; adjust based on revision scope)"

    brief = f"# Revision Brief: Chapter {ch} — {title} ({brief_type})\n\n"
    brief += "## PROBLEM\n"
    brief += "\n\n".join(problem_parts) + "\n\n"
    brief += "## WHAT TO KEEP\n"
    brief += "\n".join(keep_parts) + "\n\n"
    brief += "## WHAT TO CHANGE\n"
    brief += "\n".join(change_parts) + "\n\n"
    brief += "## VOICE RULES\n"
    brief += "\n".join(f"- {r}" for r in voice_rules) + "\n\n"
    brief += "## TARGET\n"
    brief += target_note + "\n"

    return brief
