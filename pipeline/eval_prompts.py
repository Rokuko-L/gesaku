#!/usr/bin/env python3
"""Judge prompt construction for foundation and chapter evaluation.

Genre-config driven: the dimension list and criteria come from
`active_genre.json`, so the prompts are assembled rather than static.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.genre import load_genre

def build_foundation_prompt():
    cfg = load_genre()
    ecfg = cfg["evaluation"]["foundation"]
    prompt = ecfg["overall_calibration"] + "\n\n"

    prompt += """VOICE DEFINITION:
{voice}

WORLD BIBLE:
{world}

CHARACTER REGISTRY:
{characters}

OUTLINE:
{outline}

CANON (established facts):
{canon}

CROSS-CHECKS (perform these before scoring):
1. Check all example dialogue lines against ANTI-SLOP patterns
2. Check for missing NEGATIVE SPACE
3. Check for CONVENIENT GAPS vs DELIBERATE MYSTERY
4. Check the canon for INTERNAL CONTRADICTIONS

Score these dimensions (gap + improvement required for each):

"""
    for dim in ecfg["dimensions"]:
        prompt += f"- {dim['key'].replace('_', ' ').title()}: {dim['criteria']}\n\n"

    prompt += f"""
Respond with JSON:
{{
  "overall_score": N,
  "lore_score": N,
{chr(10).join(f'  "{dim["key"]}": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},' for dim in ecfg["dimensions"])}
  "slop_in_planning_docs": {{"found": ["list any AI slop patterns"], "note": "..."}},
  "contradictions_found": ["list any factual contradictions"],
  "weakest_dimension": "...",
  "top_3_improvements": ["ranked list of improvements"]
}}

CRITICAL FORMATTING GUIDELINES:
1. Output ONLY valid JSON matching the exact schema above.
2. Escape any double quotes within your JSON string values with a backslash (e.g., use \\" instead of " when referencing characters, quotes, or dialogue).
3. Do not include any preamble, introduction, or conversation outside the JSON object.

WEIGHTING: {" + ".join(f'{dim["key"].replace("_"," ").title()} {dim["weight"]*100:.0f}%' for dim in ecfg["dimensions"])}.

FINAL CHECK: If your overall_score is above 7, re-read your gap lists.
If any gap describes a problem that would force a writer to stop and
invent something during drafting, your score is too high. Revise down.
"""
    return prompt


def build_chapter_prompt(voice, world, characters, canon_view, chapter_outline, prev_chapter_tail, chapter_text, debt_warnings=None):
    cfg = load_genre()
    ccfg = cfg["evaluation"]["chapter"]
    prompt = ccfg["overall_calibration"] + "\n\n"

    if debt_warnings:
        prompt += f"CRITICAL REQUIREMENT: This chapter MUST resolve the following active narrative setups/debts:\n"
        prompt += "\n".join(f"- {w}" for w in debt_warnings)
        prompt += "\nIf the chapter fails to clearly resolve these setups, dock the overall score significantly and explain why in your critique.\n\n"

    prompt += f"""VOICE DEFINITION:
{voice}

WORLD BIBLE (summary):
{world}

CHARACTER REGISTRY:
{characters}

CANON AS OF THIS CHAPTER (hard facts the reader may already know — violations are bugs.
Sealed author truths are withheld on purpose; do not treat missing secrets as errors,
and do not reward prose for leaking them early):
{canon_view}

CHAPTER OUTLINE ENTRY:
{chapter_outline}

PREVIOUS CHAPTER (last ~600 words):
{prev_chapter_tail}

THE CHAPTER TO EVALUATE:
{chapter_text}

CANON-GROUNDING RULES (read before scoring):
- new_canon_entries: Each entry is an object with a "fact" string and a "scope" that is either "core" or "incremental".
  - core:     Permanent world rules, character relationships, secrets, faction alignments,
              magic system rules — facts that are immutably true for the rest of the story.
  - incremental: Plot-level reveals, scene-specific reactions, temporary states, intermediate
              discoveries that later chapters may supersede or contradict.
  If in doubt, default to "incremental". Only mark as "core" if the fact is foundational
  and will never change.
  Record only what was explicitly shown or stated in this chapter's text. Never record
  background facts from the world/character bible that haven't put on the page.
- unexplained_references: Names, titles, or terms used in this chapter whose meaning
  a first-time reader would not yet understand (e.g. if a character is addressed as "the Saint"
  but the role hasn't been explained yet).
- Do not penalize the chapter for failing to reveal or use facts that are not in the
  CANON AS OF THIS CHAPTER block. Do not dock for "over-caution" about unrevealed truths.

CROSS-CHECKS (perform before scoring):
1. QUOTE TEST: Find the 3 best sentences and 3 weakest sentences.
2. DIALOGUE REALISM: Read all dialogue aloud (mentally).
3. SCENE VS SUMMARY: How much is in-scene vs summary?
4. AI PATTERN CHECK: Common AI writing patterns.
5. EARNED VS GIVEN: Is tension earned or asserted?

Score these dimensions:

"""
    for dim in ccfg["dimensions"]:
        prompt += f"- {dim['key'].replace('_', ' ').title()}: {dim['criteria']}\n\n"

    prompt += f"""
Respond with JSON:
{{
  "overall_score": N,
{chr(10).join(f'  "{dim["key"]}": {{"score": N, "weakest_moment": "...", "fix": "...", "note": "..."}},' for dim in ccfg["dimensions"])}
  "three_weakest_sentences": ["quote 1", "quote 2", "quote 3"],
  "three_strongest_sentences": ["quote 1", "quote 2", "quote 3"],
  "ai_patterns_detected": ["list any AI writing patterns found"],
  "weakest_dimension": "...",
  "top_3_revisions": ["specific revision 1", "revision 2", "revision 3"],
  "new_canon_entries": [{{"fact": "new fact description", "scope": "core|incremental"}}],
  "unexplained_references": ["names, titles, or terms used in this chapter that were not explained"]
}}

CRITICAL FORMATTING GUIDELINES:
1. Output ONLY valid JSON matching the exact schema above.
2. Escape any double quotes within your JSON string values with a backslash (e.g., use \\" instead of " when referencing characters, quotes, or dialogue).
3. Do not include any preamble, introduction, or conversation outside the JSON object.

SCORING CALIBRATION:
- Evaluate the chapter based on the overall balance of strengths and weaknesses across all dimensions. Do not let minor, easily fixable stylistic flaws or trivial editor notes artificially cap the score at 7 or below.
- Reserve scores of 8.0+ for chapters that are structurally sound, align well with the voice and characters, and successfully cover their core narrative beats.
- If a chapter has a significant, core-level failure in a defined category (such as a major plot/continuity contradiction, complete failure to cover outline beats, or major voice derailment), the overall_score should not exceed 7.0. Otherwise, score the chapter proportionally to its actual quality.
"""
    return prompt
