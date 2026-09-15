"""Revision brief from reader-panel consensus."""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import re

from core import paths
from pipeline.briefs.context import (
    chapter_text, chapter_title, extract_voice_rules, latest_chapter_eval,
    load_cuts, load_json, load_panel, panel_mentions_for_chapter,
    word_count,
)


def build_panel_brief(ch: int, extra_rules: list[str] | None = None) -> str:
    panel = load_panel()
    if panel is None:
        raise FileNotFoundError("edit_logs/reader_panel.json not found")

    text = chapter_text(ch)
    title = chapter_title(text)
    wc = word_count(text)
    info = panel_mentions_for_chapter(panel, ch)
    mentions = info["mentions"]
    flagged = info["flagged_issues"]
    voice_rules = extract_voice_rules() + (extra_rules or [])

    # Determine brief type from dominant issue
    from core.genre import load_genre
    genre_cfg = load_genre()
    outline_cfg = genre_cfg.get("generation", {}).get("outline", {})
    estimated_words = outline_cfg.get("estimated_words", 32000)
    estimated_chapters = outline_cfg.get("estimated_chapters", 10)
    target_average = estimated_words // estimated_chapters

    negative_keys = ["momentum_loss", "worst_scene", "cut_candidate"]
    neg_count = sum(len(mentions[k]) for k in negative_keys)
    if len(mentions["cut_candidate"]) > 0:
        brief_type = "COMPRESS"
    elif len(mentions["worst_scene"]) > 0:
        brief_type = "DRAMATIZE"
    elif len(mentions["momentum_loss"]) > 0:
        brief_type = "TIGHTEN"
    else:
        brief_type = "REVISE"

    if brief_type == "COMPRESS" and wc < target_average:
        # Override COMPRESS if wc is already below target average
        if len(mentions["worst_scene"]) > 0:
            brief_type = "DRAMATIZE"
        elif len(mentions["momentum_loss"]) > 0:
            brief_type = "TIGHTEN"
        else:
            brief_type = "DRAMATIZE"

    # Build PROBLEM section
    problem_parts: list[str] = []
    if flagged:
        problem_parts.append(
            "Panel disagreement flags for this chapter:\n"
            + "\n".join(f"- {f}" for f in flagged)
        )
    for key in negative_keys:
        if mentions[key]:
            problem_parts.append(f"### {key.replace('_', ' ').title()}")
            for m in mentions[key]:
                # Truncate very long quotes to ~400 chars for readability
                if len(m) > 500:
                    m = m[:500] + "..."
                problem_parts.append(m)

    if not problem_parts:
        problem_parts.append(
            f"No specific negative feedback for Chapter {ch} from the reader panel. "
            "Consider cross-referencing with --eval or --cuts for targeted feedback."
        )

    # Build WHAT TO KEEP section
    keep_parts: list[str] = []
    if mentions["best_scene"]:
        for m in mentions["best_scene"]:
            if len(m) > 500:
                m = m[:500] + "..."
            keep_parts.append(m)
    # Check cuts file for tightest_passage
    cuts_data = load_cuts(ch)
    if cuts_data and cuts_data.get("tightest_passage"):
        keep_parts.append(
            f'Tightest passage (from adversarial edit): "{cuts_data["tightest_passage"]}"'
        )
    # Check per-chapter eval for strongest sentences
    ch_eval_path = latest_chapter_eval(ch)
    if ch_eval_path:
        ch_eval = load_json(ch_eval_path)
        strongest = ch_eval.get("three_strongest_sentences", [])
        if strongest:
            keep_parts.append("Strongest sentences (from eval):")
            for s in strongest:
                keep_parts.append(f'- "{s}"')

    if not keep_parts:
        keep_parts.append(
            f"(No specific 'best' mentions for Chapter {ch}. "
            "Review the chapter for its strongest passages before revising.)"
        )

    # Build WHAT TO CHANGE section
    change_parts: list[str] = []
    change_num = 1

    # From momentum_loss
    for m in mentions["momentum_loss"]:
        # Extract actionable suggestion if present
        change_parts.append(
            f"{change_num}. **Pacing**: Address momentum loss identified by panel — "
            "tighten or restructure the scenes that drag."
        )
        change_num += 1
        break  # one entry is enough

    # From worst_scene
    for m in mentions["worst_scene"]:
        # Try to extract the fix suggestion — look for "Fix:" or "The fix is"
        fix_match = re.search(
            r"(?:The fix(?:\s+is\s*\w*)?|Fix)\s*[:—]\s*(.+)",
            m, re.I | re.DOTALL
        )
        if fix_match:
            # Take up to ~300 chars of the fix suggestion
            raw_fix = fix_match.group(1).strip()
            fix_text = (raw_fix[:300] + "...") if len(raw_fix) > 300 else raw_fix
            fix_text = fix_text.rstrip(".")
        else:
            # Fall back to the full worst_scene comment, truncated
            raw = m.split("]", 1)[-1].strip() if "]" in m else m
            fix_text = (raw[:300] + "...") if len(raw) > 300 else raw
        change_parts.append(f"{change_num}. **Dramatize**: {fix_text}")
        change_num += 1
        break

    # From cut_candidate
    for m in mentions["cut_candidate"]:
        change_parts.append(
            f"{change_num}. **Compress**: Panel identifies this chapter as a cut candidate. "
            "Fold essential beats into fewer words; eliminate repeated exposition."
        )
        change_num += 1
        break

    # From thinnest_character
    if mentions["thinnest_character"]:
        change_parts.append(
            f"{change_num}. **Deepen character**: Panel flags thin characterization in this chapter. "
            "Add interiority, physical specificity, or a complicating moment."
        )
        change_num += 1

    # From missing_scene
    if mentions["missing_scene"]:
        change_parts.append(
            f"{change_num}. **Add missing beat**: Panel identifies a scene gap near this chapter."
        )
        for m in mentions["missing_scene"]:
            snippet = m[:300] + "..." if len(m) > 300 else m
            change_parts.append(f"   {snippet}")
        change_num += 1

    if not change_parts:
        change_parts.append(
            "No specific changes derived from panel. "
            "Consider combining with --eval or --cuts for concrete revision items."
        )

    # Determine word count target
    if brief_type == "COMPRESS":
        target_wc = int(wc * 0.55)
        target_note = f"~{target_wc} words (compress from current {wc})"
    elif brief_type == "DRAMATIZE":
        target_wc = wc  # restructure, not expand
        target_note = f"~{target_wc} words (restructure, roughly same length)"
    elif brief_type == "TIGHTEN":
        target_wc = int(wc * 0.85)
        target_note = f"~{target_wc} words (tighten from current {wc})"
    else:
        target_wc = wc
        target_note = f"~{wc} words (current length, unless changes dictate otherwise)"

    # Enforce hard floor of 60% of per-chapter target (average)
    floor_wc = int(target_average * 0.60)
    if target_wc < floor_wc:
        target_wc = floor_wc
        target_note = f"~{target_wc} words (expand from current {wc} to meet minimum length of {floor_wc})"

    # Assemble
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
