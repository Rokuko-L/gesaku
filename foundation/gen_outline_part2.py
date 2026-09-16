#!/usr/bin/env python3
"""Refine and expand outline.md block-by-block to add foreshadowing and plants/harvests."""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import TruncationError, call_llm, get_max_tokens_with_thinking
from core.paths import format_prompt
import os
import sys
import re
import json
from pathlib import Path
from dotenv import load_dotenv
from core.genre import load_genre, chapters_total as genre_chapters_total
from core import paths

load_dotenv()

def call_writer(prompt, max_tokens=get_max_tokens_with_thinking(16000)):
    return call_llm(prompt=prompt, model_key="writer", max_tokens=max_tokens, beta_context=True, timeout_role="standard")

def validate_block_output(text, start, end):
    missing = []
    for ch in range(start, end + 1):
        pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{ch}\b'
        if not re.search(pattern, text, re.IGNORECASE):
            missing.append(f"Chapter {ch}")
    if missing:
        return False, f"Missing detailed outlines for: {', '.join(missing)}"
    return True, ""

def main():
    root = paths.get_root_dir()
    outline_path = paths.get_outline_path()
    roadmap_path = paths.get_outline_roadmap_path()

    if not outline_path.exists():
        print(f"ERROR: outline.md not found at {outline_path} — run gen_outline.py first", file=sys.stderr)
        sys.exit(1)

    outline_text = outline_path.read_text(encoding="utf-8")
    roadmap = roadmap_path.read_text(encoding="utf-8") if roadmap_path.exists() else ""

    world = (paths.get_world_path()).read_text(encoding="utf-8")
    characters = (paths.get_characters_path()).read_text(encoding="utf-8")
    voice = (paths.get_voice_path()).read_text(encoding="utf-8")
    
    # Extract voice part 2
    voice_lines = voice.split('\n')
    try:
        part2_start = next(i for i, l in enumerate(voice_lines) if 'Part 2' in l)
        voice_part2 = '\n'.join(voice_lines[part2_start:])
    except StopIteration:
        voice_part2 = voice

    genre_cfg = load_genre()
    
    try:
        state = json.loads((paths.get_project_dir() / "state.json").read_text(encoding="utf-8"))
        title = state.get("title", "Untitled Novel")
    except Exception:
        state = {}
        title = "Untitled Novel"
    total_chapters = (
        genre_chapters_total()
        or int(state.get("chapters_total") or 0)
        or 24
    )

    # Extract all unpolished chapters (only from Detailed section)
    unpolished_chapters = {}
    if "## DETAILED CHAPTER OUTLINES" in outline_text:
        detailed_section = outline_text.split("## DETAILED CHAPTER OUTLINES", 1)[1]
        for ch in range(1, total_chapters + 1):
            pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{ch}\b.*?(?=###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*(?:\d+)\b|## Act|## Foreshadowing|$)'
            match = re.search(pattern, detailed_section, re.IGNORECASE | re.DOTALL)
            if match:
                unpolished_chapters[ch] = match.group(0).strip()
            else:
                print(f"WARNING: Chapter {ch} not found in outline.md Detailed section during refinement preparation.", file=sys.stderr)
    else:
        print("ERROR: ## DETAILED CHAPTER OUTLINES section not found in outline.md during refinement preparation.", file=sys.stderr)
        sys.exit(1)

    block_size = 10
    blocks = []
    for start in range(1, total_chapters + 1, block_size):
        end = min(start + block_size - 1, total_chapters)
        blocks.append((start, end))

    polished_outlines = {}

    for start, end in blocks:
        print(f"Refining Block Ch {start}-{end}...", file=sys.stderr)
        
        # Build unpolished block text
        unpolished_block = "\n\n".join(unpolished_chapters[ch] for ch in range(start, end + 1) if ch in unpolished_chapters)
        
        # Build previous polished context (for local continuity)
        prev_polished = ""
        if start > 1:
            prev_start = max(1, start - block_size)
            prev_polished = "\n\n".join(polished_outlines[ch] for ch in range(prev_start, start) if ch in polished_outlines)

        prompt = f"""You are a master story pacing editor. Your task is to refine and polish the detailed chapter outlines for Chapters {start} through {end} of "{title}".

GLOBAL ROADMAP & THREAD LEDGER:
{roadmap}

WORLD BIBLE:
{world}

CHARACTER REGISTRY:
{characters}

VOICE STYLE GUIDE:
{voice_part2}

PREVIOUS POLISHED CHAPTER OUTLINES (for local continuity):
{prev_polished}

CURRENT UNPOLISHED CHAPTER OUTLINES:
{unpolished_block}

TASK:
Refine the unpolished chapter outlines for Chapters {start} through {end}.
Focus on:
1. **Scene Pacing**: Ensure each chapter's scene beats are sequential, detailed (3-4 sentences per beat), and advance the plot.
2. **Plants and Harvests**: Ensure every chapter includes specific plants (setup) and harvests (payoffs) tagged exactly as `[Plant: slug_name - "Description"]` or `[Harvest: slug_name - "Description"]`, aligning with the Global Plot Threads Ledger.
3. **Tone and Style**: Ensure the prose instructions match the clinical, high-interiority guidelines.

FORMAT REQUIREMENT:
Write the refined outlines in markdown.
Each chapter outline must start with a heading: "### Chapter N: [Chapter Title]". Do not write any other chapters outside of Chapters {start} through {end}.
"""
        block_result = ""
        for attempt in range(1, 4):
            try:
                res = call_writer(prompt)
            except TruncationError as e:
                print(f"  WARN: Refinement Block Ch {start}-{end} attempt {attempt} truncated ({e}), retrying...", file=sys.stderr)
                continue
            passed, err = validate_block_output(res, start, end)
            if passed:
                block_result = res
                break
            print(f"  WARN: Refinement Block Ch {start}-{end} failed validation on attempt {attempt}/3: {err}. Retrying...", file=sys.stderr)
            prompt += f"\n\nERROR ON ATTEMPT {attempt}: {err}\nEnsure you return refined outlines for all chapters from {start} to {end}."
            
        if not block_result:
            print(f"ERROR: Failed to refine Block Ch {start}-{end}.", file=sys.stderr)
            sys.exit(1)

        # Parse and save the block chapters to polished_outlines
        for ch in range(start, end + 1):
            pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{ch}\b.*?(?=###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*(?:\d+)\b|## Act|## Foreshadowing|$)'
            match = re.search(pattern, block_result, re.IGNORECASE | re.DOTALL)
            if match:
                polished_outlines[ch] = match.group(0).strip()
            else:
                print(f"ERROR: Could not isolate Chapter {ch} refined outline from block output.", file=sys.stderr)
                sys.exit(1)

        # Save active block progress in outline.md immediately
        full_outline_text = f"# {title.upper()}\n\n" + roadmap + "\n\n## DETAILED CHAPTER OUTLINES\n\n" + \
                            "\n\n---\n\n".join(polished_outlines[ch] for ch in sorted(polished_outlines.keys()))
        outline_path.write_text(full_outline_text, encoding="utf-8")

    # Final assembly and save
    full_outline_text = f"# {title.upper()}\n\n" + roadmap + "\n\n## DETAILED CHAPTER OUTLINES\n\n" + \
                        "\n\n---\n\n".join(polished_outlines[ch] for ch in sorted(polished_outlines.keys()))
    outline_path.write_text(full_outline_text, encoding="utf-8")
    
    # Save a copy as .outline_part1.md for backwards compatibility
    paths.get_outline_part1_path().write_text(full_outline_text, encoding="utf-8")

    # Checkpoint marker: the foundation loop skips this pass only when this
    # file exists. Written last, so an interrupted run re-runs the polish.
    paths.get_outline_part2_path().write_text("done\n", encoding="utf-8")

    print("Outline refinement complete!", file=sys.stderr)

if __name__ == "__main__":
    main()