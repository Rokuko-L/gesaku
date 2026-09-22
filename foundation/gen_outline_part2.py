#!/usr/bin/env python3
"""Refine and expand outline.md block-by-block to add foreshadowing and plants/harvests."""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import (
    REFINEMENT_ATTEMPTS, REFINEMENT_BLOCK_SIZE,
    TruncationError, call_llm, get_max_tokens_with_thinking,
)
import sys
import re
import json
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
    chapters_total = genre_chapters_total() if genre_cfg else 0
    try:
        state = json.loads((paths.get_project_dir() / "state.json").read_text(encoding="utf-8"))
        title = state.get("title", "Untitled Novel")
    except Exception:
        state = {}
        title = "Untitled Novel"
    total_chapters = (
        chapters_total
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
        print("ERROR: ## DETAILED CHAPTER OUTLINES section not found in outline.md during refinement preparation.", file=sys.stderr)
        sys.exit(1)

    missing_source = [ch for ch in range(1, total_chapters + 1) if ch not in unpolished_chapters]
    if missing_source:
        # gen_outline.py writes the detailed section in blocks, so a truncated
        # or partially-healed part 1 reaches here with the tail absent. Say so
        # once with the chapter list rather than per chapter: twenty printed
        # lines buried the rest of the run log and hid that blocks were skipped.
        print(f"WARNING: {len(missing_source)} of {total_chapters} chapters are absent from "
              f"the Detailed section (part 1 incomplete): chapters {missing_source}",
              file=sys.stderr)

    block_size = REFINEMENT_BLOCK_SIZE
    blocks = []
    for start in range(1, total_chapters + 1, block_size):
        end = min(start + block_size - 1, total_chapters)
        blocks.append((start, end))

    polished_outlines = {}
    skipped_blocks = []
    failed_blocks = []

    for start, end in blocks:
        block_chapters = [ch for ch in range(start, end + 1) if ch in unpolished_chapters]
        if not block_chapters:
            # Nothing to refine: spending 3 writer calls on an empty block is
            # pure waste, and its failure would abort a run over chapters that
            # were never in this pass to begin with.
            print(f"SKIP: Block Ch {start}-{end} has no source chapters.", file=sys.stderr)
            skipped_blocks.append((start, end))
            continue

        print(f"Refining Block Ch {start}-{end}...", file=sys.stderr)
        
        # Build unpolished block text
        unpolished_block = "\n\n".join(unpolished_chapters[ch] for ch in block_chapters)
        
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
        last_err = ""
        for attempt in range(1, REFINEMENT_ATTEMPTS + 1):
            try:
                res = call_writer(prompt)
            except TruncationError as e:
                last_err = f"truncated ({e})"
                print(f"  WARN: Refinement Block Ch {start}-{end} attempt {attempt} truncated ({e}), retrying...", file=sys.stderr)
                continue
            passed, err = validate_block_output(res, start, end)
            if passed:
                block_result = res
                break
            last_err = err
            print(f"  WARN: Refinement Block Ch {start}-{end} failed validation on attempt {attempt}/{REFINEMENT_ATTEMPTS}: {err}. Retrying...", file=sys.stderr)
            prompt += f"\n\nERROR ON ATTEMPT {attempt}: {err}\nEnsure you return refined outlines for all chapters from {start} to {end}."

        if not block_result:
            failed_blocks.append((start, end, last_err))
            continue

        # Parse and save the block chapters to polished_outlines
        for ch in block_chapters:
            pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{ch}\b.*?(?=###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*(?:\d+)\b|## Act|## Foreshadowing|$)'
            match = re.search(pattern, block_result, re.IGNORECASE | re.DOTALL)
            if match:
                polished_outlines[ch] = match.group(0).strip()
            else:
                # The block validated, so this chapter heading exists in raw
                # form; falling back to the unpolished source keeps the
                # chapter rather than aborting the whole pass over one heading.
                polished_outlines[ch] = unpolished_chapters[ch]
                print(f"  WARN: could not isolate Chapter {ch} from block output; "
                      f"keeping its unpolished text.", file=sys.stderr)

        # Save active block progress in outline.md immediately
        full_outline_text = f"# {title.upper()}\n\n" + roadmap + "\n\n## DETAILED CHAPTER OUTLINES\n\n" + \
                            "\n\n---\n\n".join(polished_outlines[ch] for ch in sorted(polished_outlines.keys()))
        outline_path.write_text(full_outline_text, encoding="utf-8")

    # Chapters whose blocks failed keep their unpolished text: the pass adds
    # foreshadowing and plant tags, it does not author chapters. Shipping the
    # unpolished versions is what makes a partial refinement usable — but the
    # caller has to hear about it, because the plant ledger below is what the
    # reveal retrofit and plant hygiene read.
    for ch in sorted(unpolished_chapters):
        polished_outlines.setdefault(ch, unpolished_chapters[ch])

    # Final assembly and save
    full_outline_text = f"# {title.upper()}\n\n" + roadmap + "\n\n## DETAILED CHAPTER OUTLINES\n\n" + \
                        "\n\n---\n\n".join(polished_outlines[ch] for ch in sorted(polished_outlines.keys()))
    outline_path.write_text(full_outline_text, encoding="utf-8")
    
    # Save a copy as .outline_part1.md for backwards compatibility
    paths.get_outline_part1_path().write_text(full_outline_text, encoding="utf-8")

    if skipped_blocks:
        print(f"SKIPPED: {len(skipped_blocks)} block(s) had no source chapters: "
              f"{[f'Ch {a}-{b}' for a, b in skipped_blocks]}", file=sys.stderr)

    missing_after = [ch for ch in range(1, total_chapters + 1)
                     if ch not in polished_outlines]
    if failed_blocks or missing_after:
        # The completion marker is the phase's checkpoint, so it must not claim
        # a pass that did not finish. The outline above is already on disk with
        # whatever was polished; leaving the marker unwritten makes the next
        # resume re-run the polish. Exiting non-zero is deliberate:
        # `uv_run(check=True)` aborts the foundation phase, because an
        # unrefined outline is what plant hygiene, the reveal retrofit and the
        # drafting prompt all read. A skipped block lands here too — a block
        # with no source chapters means part 1 never wrote them, and reporting
        # success would let `_foundation_part2_ok` disagree with the marker.
        detail = "; ".join(f"Ch {a}-{b} ({err})" for a, b, err in failed_blocks) or "none"
        print(f"ERROR: outline refinement incomplete — {len(failed_blocks)} failed "
              f"block(s): {detail}. Missing chapters: {missing_after} "
              f"(skipped blocks: {[f'Ch {a}-{b}' for a, b in skipped_blocks]}). "
              f"The marker was NOT written.", file=sys.stderr)
        sys.exit(1)

    # Checkpoint marker: the foundation loop skips this pass only when this
    # file exists. Written last, so an interrupted run re-runs the polish.
    paths.get_outline_part2_path().write_text("done\n", encoding="utf-8")

    print(f"Outline refinement complete! ({len(polished_outlines)} chapters)",
          file=sys.stderr)

if __name__ == "__main__":
    main()