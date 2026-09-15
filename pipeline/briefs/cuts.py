"""Revision brief from adversarial cut recommendations."""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

from core import paths
from pipeline.briefs.context import (
    chapter_text, chapter_title, extract_voice_rules, latest_chapter_eval,
    load_cuts, load_json, word_count,
)


def build_cuts_brief(ch: int, extra_rules: list[str] | None = None) -> str:
    cuts_data = load_cuts(ch)
    if cuts_data is None:
        raise FileNotFoundError(f"edit_logs/ch{ch:02d}_cuts.json not found")

    text = chapter_text(ch)
    title = chapter_title(text)
    wc = word_count(text)
    voice_rules = extract_voice_rules() + (extra_rules or [])

    cuts = cuts_data.get("cuts", [])
    total_cuttable = cuts_data.get("total_cuttable_words", 0)
    tightest = cuts_data.get("tightest_passage", "")
    loosest = cuts_data.get("loosest_passage", "")
    fat_pct = cuts_data.get("overall_fat_percentage", 0)
    verdict = cuts_data.get("one_sentence_verdict", "")

    # Categorize cuts by type
    cut_types: dict[str, list[dict]] = {}
    for c in cuts:
        t = c.get("type", "OTHER")
        cut_types.setdefault(t, []).append(c)

    # Determine dominant pattern
    type_counts = {t: len(cs) for t, cs in cut_types.items()}
    dominant = max(type_counts, key=type_counts.get) if type_counts else "MIXED"

    brief_type = "TIGHTEN"

    # PROBLEM
    problem_parts: list[str] = []
    problem_parts.append(
        f"Adversarial edit found **{total_cuttable} cuttable words** "
        f"({fat_pct}% fat) across {len(cuts)} passages."
    )
    if verdict:
        problem_parts.append(f"Verdict: {verdict}")

    problem_parts.append(f"\nDominant cut pattern: **{dominant}** ({type_counts.get(dominant, 0)} instances)")
    for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        if t != dominant:
            problem_parts.append(f"- {t}: {count} instances")

    if loosest:
        problem_parts.append(f'\n**Loosest passage:**\n> {loosest}')

    # WHAT TO KEEP
    keep_parts: list[str] = []
    if tightest:
        keep_parts.append(f'**Tightest passage** (do not touch):\n> {tightest}')

    # Also pull strongest sentences from eval if available
    ch_eval_path = latest_chapter_eval(ch)
    if ch_eval_path:
        ch_eval = load_json(ch_eval_path)
        strongest = ch_eval.get("three_strongest_sentences", [])
        if strongest:
            keep_parts.append("\nStrongest sentences (from eval):")
            for s in strongest:
                keep_parts.append(f'- "{s}"')

    if not keep_parts:
        keep_parts.append("(Review chapter for strongest passages before revising.)")

    # WHAT TO CHANGE — specific numbered items from each cut
    change_parts: list[str] = []
    change_num = 1

    # Group by type for clarity
    for cut_type in ["REDUNDANT", "OVER-EXPLAIN", "FAT", "TELL", "GENERIC", "OTHER"]:
        type_cuts = cut_types.get(cut_type, [])
        if not type_cuts:
            continue
        change_parts.append(f"\n### {cut_type} ({len(type_cuts)} cuts)")
        for c in type_cuts:
            quote = c.get("quote", "")
            reason = c.get("reason", "")
            action = c.get("action", "CUT")
            rewrite = c.get("rewrite")

            # Truncate very long quotes
            if len(quote) > 200:
                quote = quote[:200] + "..."

            entry = f'{change_num}. `"{quote}"`\n'
            entry += f"   Reason: {reason}\n"
            if action == "REWRITE" and rewrite:
                entry += f'   → Rewrite as: "{rewrite}"'
            elif action == "CUT":
                entry += "   → Cut entirely"
            change_parts.append(entry)
            change_num += 1

    # Word count target
    target_wc = wc - total_cuttable
    target_note = (
        f"~{target_wc} words (cut ~{total_cuttable} from current {wc}). "
        f"Tighten {fat_pct}% fat without losing the chapter's strongest beats."
    )

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

    return brief
