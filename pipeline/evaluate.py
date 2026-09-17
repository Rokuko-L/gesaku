#!/usr/bin/env python3
"""
evaluate.py -- Novel evaluation harness.

Usage:
  python evaluate.py --phase=foundation    # Score planning docs only
  python evaluate.py --chapter=5           # Score a single chapter
  python evaluate.py --full                # Score the entire novel

Output: structured scores to stdout + eval_logs/<timestamp>.json

This file is READ-ONLY during autonomous runs. The human edits it
to tune what "good" means. The agent treats it as a black box.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import TruncationError, call_llm, extract_text_from_response, get_max_tokens_with_thinking, parse_json_response
from core import paths
from core import textstats
from core.outline import open_debts_for_chapter
from core import canon as canon_mod
import argparse
import json
import os
import sys
import glob
import re
from datetime import datetime
from pathlib import Path

# --- Configuration ---

# Load .env file if present
from dotenv import load_dotenv
load_dotenv()
from core.genre import load_genre
from core import validation
from pipeline.slop import slop_score
from pipeline.orientation import check_orientation_facts
from pipeline.eval_prompts import build_chapter_prompt, build_foundation_prompt




def load_file(path):
    """Load a text file, return empty string if missing, with robust encoding recovery and self-healing."""
    path = Path(path)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_bytes()
        for enc in ("utf-16", "utf-16-le", "utf-16-be", "latin-1"):
            try:
                text = raw.decode(enc).lstrip("\ufeff")
                # Self-heal: rewrite as clean UTF-8
                path.write_text(text, encoding="utf-8")
                print(f"[ENCODING] Repaired {path.name}: was {enc}, now UTF-8", file=sys.stderr)
                return text
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Could not decode {path} with any known encoding")


def load_layer_files():
    """Load all planning layer files from the active project directory."""
    return {
        "voice": load_file(paths.get_voice_path()),
        "world": load_file(paths.get_world_path()),
        "characters": load_file(paths.get_characters_path()),
        "outline": load_file(paths.get_outline_path()),
        "canon": load_file(paths.get_canon_path()),
    }


def load_chapter(n):
    """Load a single chapter file from the active project."""
    return load_file(paths.get_chapters_dir() / f"ch_{n:02d}.md")


def load_all_chapters():
    """Load all chapter files in order from the active project."""
    chapters_dir = paths.get_chapters_dir()
    chapters = {}
    for f in sorted(glob.glob(str(chapters_dir / "ch_*.md"))):
        num = int(re.search(r'ch_(\d+)', f).group(1))
        try:
            chapters[num] = load_file(Path(f))
        except ValueError as e:
            raise RuntimeError(f"FATAL: chapter file {f} (ch {num}) is unreadable: {e}")
    return chapters


def call_judge(prompt, max_tokens=2000):
    genre_cfg = load_genre()
    system = genre_cfg["identity"]["evaluator_system"]
    perspective = genre_cfg.get("perspective", "")
    if perspective:
        expected = "first-person ('I/me/my' narration by the POV character)" if perspective == "first_person" else "third-person limited (he/she/they, anchored to the POV character's head)"
        system += (f"\n\nPERSPECTIVE RULE: The novel is mandated {expected}. If the chapter drifts "
                   "out of this narration mode, flag it under prose_quality or voice_adherence "
                   "with a specific quote of the offending passage.")
    from core.genre import prose_mode_system_block
    prose_block = prose_mode_system_block(genre_cfg)
    if prose_block:
        system += (prose_block +
                   "\n\nPROSE MODE RULE: Score against this pack. Penalize staccato 1–4 word "
                   "paragraph stacks, empty emotion labels, diary-summary interiority, wrong "
                   "narrative distance, and repeated stock metaphors. Quote offenders.")
    return call_llm(prompt=prompt, system=system, model_key="judge", max_tokens=max_tokens, beta_context=True, timeout_role="xlong")


def call_judge_json(prompt, max_tokens=8000, retries=3, model=None):
    """Call the judge and return its JSON as a dict.

    When `model` (a validation.ScoreOutput / NovelScoreOutput subclass) is
    given, each response is schema-validated; shape failures join syntax
    failures in the LLM self-correction retry loop. The validated model is
    returned as a dict so downstream dict access keeps working.
    """
    last_raw = None
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            if attempt == 1 or not last_raw or not last_raw.strip():
                raw = call_judge(prompt, max_tokens)
            else:
                # Ask the model to fix its previous response (using a cheap, lightweight context prompt)
                fix_prompt = f"""You previously returned a response that had invalid JSON syntax.
The parser returned this error: {last_error}

YOUR PREVIOUS RESPONSE:
{last_raw}

TASK:
Correct the JSON syntax errors in your previous response. Respond ONLY with the corrected, valid JSON object. Do not include any explanation or conversational text outside the JSON. Ensure all quotes inside string values are properly escaped (e.g. use \\" instead of ")."""
                # Dynamically calculate a token limit for the fix call
                tokens_needed = max(2000, (len(last_raw) // 3) + 200)
                max_tokens_fix = min(max_tokens, tokens_needed)
                
                raw = call_judge(fix_prompt, max_tokens_fix)
            
            last_raw = raw
            result = parse_json_response(raw)
            if model is not None:
                validated = validation.parse_validated(model, raw, context="Judge response")
                return validated.model_dump()
            return result
        except TruncationError:
            # Response hit the token cap. Retrying with the SAME-or-smaller budget is
            # guaranteed to truncate again — re-ask the original prompt with more room.
            if attempt == retries:
                raise
            print(f"Judge response truncated on attempt {attempt}/{retries} — "
                  f"retrying original prompt with a larger output budget "
                  f"({max_tokens} -> {int(max_tokens * 1.5)})", file=sys.stderr)
            max_tokens = int(max_tokens * 1.5)
            last_raw = None
        except (json.JSONDecodeError, ValueError) as e:
            last_error = str(e)
            if attempt == retries:
                raise e
            print(f"JSON decode failed on attempt {attempt}/{retries}: {e}. Retrying LLM self-correction...", file=sys.stderr)


# --- Foundation Evaluation ---

def evaluate_foundation():
    layers = load_layer_files()
    prompt = build_foundation_prompt()
    for key, val in layers.items():
        prompt = prompt.replace(f"{{{key}}}", val)
    return call_judge_json(prompt, max_tokens=16000, model=validation.ScoreOutput)


# --- Chapter Evaluation ---

def evaluate_chapter(chapter_num):
    layers = load_layer_files()
    chapter_text = load_chapter(chapter_num)
    if not chapter_text.strip():
        return {"error": f"Chapter {chapter_num} is empty or missing",
                "overall_score": 0.0}

    # Extract this chapter's outline entry — scoped to the DETAILED section so the
    # HIGH-LEVEL ROADMAP one-liner is never judged as the real beats entry.
    outline = layers["outline"]
    if "## DETAILED CHAPTER OUTLINES" in outline:
        outline = outline.split("## DETAILED CHAPTER OUTLINES", 1)[1]
    ch_pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{chapter_num}\b.*?(?=###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*(?:\d+)\b|## Act|## Foreshadowing|$)'
    ch_match = re.search(ch_pattern, outline, re.IGNORECASE | re.DOTALL)
    if not ch_match:
        raise ValueError(
            f"Chapter {chapter_num} outline entry not found in the "
            f"## DETAILED CHAPTER OUTLINES section — cannot evaluate against an empty outline."
        )
    chapter_outline = ch_match.group(0)

    # Load previous chapter tail (600-word, sentence-boundary trimmed)
    prev_text = load_chapter(chapter_num - 1) if chapter_num > 1 else "(first chapter)"
    prev_tail = textstats.tail_context(prev_text, max_words=600) if chapter_num > 1 else prev_text

    # Reader-knowledge view: public foundation + core + prior As-of. Sealed facts withheld.
    canon_view = canon_mod.judge_view_md(canon_mod.parse_canon(layers["canon"]), chapter_num)

    # Setups the outline opened and never scheduled a payoff for. Same source as
    # the drafter's, so the judge is told what the drafter was asked to do. The
    # old code matched this chapter's *harvest* slugs against the debt strings,
    # which can never be equal — a debt has no harvest anywhere.
    active_debts_to_resolve = []
    try:
        state_path = paths.get_project_dir() / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        active_debts_to_resolve = [
            f"(set up in ch{d['chapter']}) {d['desc']}"
            for d in open_debts_for_chapter(state.get("debts", []), chapter_num)
        ]
    except (OSError, ValueError) as e:
        print(f"WARN: could not read narrative debts: {e}", file=sys.stderr)

    prompt = build_chapter_prompt(
        voice=layers["voice"],
        world=layers["world"][:4000],  # truncate world bible
        characters=layers["characters"],
        canon_view=canon_view,
        chapter_outline=chapter_outline,
        prev_chapter_tail=prev_tail,
        chapter_text=chapter_text,
        debt_warnings=active_debts_to_resolve,
    )
    result = call_judge_json(prompt, max_tokens=8000, model=validation.ScoreOutput)

    # Mechanical slop check -- adjusts score independently of judge
    slop = slop_score(chapter_text)
    result["slop"] = slop
    if "overall_score" in result:
        adjusted = max(0, result["overall_score"] - slop["slop_penalty"])
        
        # Word count penalty
        genre_cfg = load_genre()
        estimated_words = genre_cfg["generation"]["outline"]["estimated_words"]
        chapter_count = genre_cfg["generation"]["outline"]["estimated_chapters"]
        target_words = estimated_words // chapter_count
        actual_words = len(chapter_text.split())
        
        # Word count penalty with climax/finale buffer
        length_penalty = 0.0
        tolerance_min = int(target_words * 0.8)
        
        is_climax = False
        if chapter_num == chapter_count:
            is_climax = True
        else:
            if "climax" in chapter_outline.lower() or "battle" in chapter_outline.lower() or "final" in chapter_outline.lower() or "coup" in chapter_outline.lower():
                is_climax = True
                
        if is_climax:
            tolerance_max = int(target_words * 1.55) # ~5,000 words ceiling
            print(f"  [INFO] Climax chapter detected: higher length ceiling allowed ({tolerance_max} words)", file=sys.stderr)
        else:
            tolerance_max = int(target_words * 1.25) # 4,000 words ceiling
            
        if actual_words < tolerance_min:
            length_penalty = max(0, (1 - actual_words / tolerance_min)) * 3.0
            adjusted = max(0, adjusted - length_penalty)
        elif actual_words > tolerance_max:
            length_penalty = max(0, (actual_words / tolerance_max - 1)) * 3.0
            adjusted = max(0, adjusted - length_penalty)
            
        # Orientation facts check
        failed_facts = check_orientation_facts(chapter_text, chapter_outline)
        orientation_penalty = 0.0
        if len(failed_facts) >= 2:
            orientation_penalty = min(len(failed_facts) * 1.0, 2.0)
            adjusted = max(0, adjusted - orientation_penalty)
            print(f"  [ORIENTATION] FAILED: {len(failed_facts)} fact(s) not dramatized: {failed_facts} — penalty: -{orientation_penalty:.2f}", file=sys.stderr)
            result["orientation_failed_facts"] = failed_facts
            
        print(f"  [LENGTH] {actual_words}/{target_words} words — penalty: -{length_penalty:.2f}", file=sys.stderr)
        result["length_penalty"] = length_penalty
        result["orientation_penalty"] = orientation_penalty
        result["raw_judge_score"] = result["overall_score"]
        result["overall_score"] = round(adjusted, 2)

    return result


# --- Full Novel Evaluation ---

FULL_NOVEL_PROMPT = paths.load_prompt("evaluate_full_novel")


def evaluate_full():
    layers = load_layer_files()
    chapters = load_all_chapters()

    if not chapters:
        return {"error": "No chapters found", "novel_score": 0.0}

    # Build chapter metadata (word count + per-chapter score)
    metadata = []
    for num in sorted(chapters.keys()):
        text = chapters[num]
        word_count = len(text.split())
        ch_score = _latest_chapter_score(num)
        score_line = f"Score: {ch_score}/10" if ch_score is not None else "Score: (not yet evaluated)"
        title = ""
        first_line = text.strip().split('\n')[0] if text.strip() else ""
        if first_line.startswith("#"):
            title = f" - Title: {first_line.lstrip('#').strip()}"
        metadata.append(
            f"Chapter {num}{title} ({word_count} words):\n"
            f"  {score_line}"
        )

    prompt = FULL_NOVEL_PROMPT.format(
        voice=layers["voice"],
        world_summary=layers["world"][:3000],
        characters=layers["characters"],
        outline=layers["outline"],
        chapter_summaries="\n".join(metadata),
    )

    result = call_judge_json(prompt, model=validation.NovelScoreOutput)

    # Apply mechanical slop penalty across the full manuscript text
    full_text = "\n\n".join(chapters.get(i, "") for i in sorted(chapters.keys()))
    slop = slop_score(full_text)
    result["full_slop"] = slop
    if "novel_score" in result:
        adjusted = max(0, result["novel_score"] - slop["slop_penalty"])
        result["raw_novel_score"] = result["novel_score"]
        result["novel_score"] = round(adjusted, 2)
        result["slop_penalty_applied"] = slop["slop_penalty"]

    return result


def _latest_chapter_score(ch_num: int) -> float | None:
    """Look up the most recent per-chapter eval score for chapter N from eval logs."""
    eval_log_dir = paths.get_eval_logs_dir()
    pattern = f"*_ch{ch_num:02d}.json"
    matches = sorted(eval_log_dir.glob(pattern))
    if not matches:
        return None
    try:
        data = json.loads(matches[-1].read_text(encoding="utf-8"))
        return data.get("overall_score")
    except (json.JSONDecodeError, OSError, KeyError):
        return None


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Evaluate the novel")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--phase", choices=["foundation"],
                       help="Evaluate planning documents")
    group.add_argument("--chapter", type=int,
                       help="Evaluate a specific chapter number")
    group.add_argument("--full", action="store_true",
                       help="Evaluate the entire novel")
    parser.add_argument("--project", default=None, help="Project name (under projects/)")
    args = parser.parse_args()

    if args.project:
        paths.set_project_name(args.project)

    if args.phase == "foundation":
        result = evaluate_foundation()
        score_key = "overall_score"
    elif args.chapter is not None:
        result = evaluate_chapter(args.chapter)
        score_key = "overall_score"
    elif args.full:
        result = evaluate_full()
        score_key = "novel_score"

    # Print structured output
    print("---")
    if score_key in result:
        print(f"{score_key}: {result[score_key]}")
    for key, val in result.items():
        if key == score_key:
            continue
        if isinstance(val, dict):
            print(f"{key}: {val.get('score', 'N/A')} -- {val.get('note', '')}")
        else:
            print(f"{key}: {val}")

    # Save full eval log
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = args.phase or (f"ch{args.chapter:02d}" if args.chapter is not None else "full")
    eval_log_dir = paths.get_eval_logs_dir()  # also creates the directory
    log_path = eval_log_dir / f"{timestamp}_{mode}.json"
    paths.save_json_atomic(result, log_path)
    print(f"\neval_log: {log_path}")


if __name__ == "__main__":
    main()
