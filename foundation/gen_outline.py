#!/usr/bin/env python3
"""Generate outline.md in a robust, act-by-act chunked fashion."""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import TruncationError, call_llm, get_max_tokens_with_thinking
from core.paths import format_prompt
import argparse
import os
import sys
import re
import json
from pathlib import Path
from dotenv import load_dotenv
from core.genre import load_genre
from core import paths

load_dotenv()

def call_writer(prompt, max_tokens=get_max_tokens_with_thinking(16000)):
    # Local thinking-proxy outline blocks routinely need >600s.
    return call_llm(prompt=prompt, model_key="writer", max_tokens=max_tokens, beta_context=True, timeout=1200)

def validate_block_output(text, start, end):
    missing = []
    for ch in range(start, end + 1):
        pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{ch}\b'
        if not re.search(pattern, text, re.IGNORECASE):
            missing.append(f"Chapter {ch}")
    if missing:
        return False, f"Missing detailed outlines for: {', '.join(missing)}"
    return True, ""

def _act_ranges(total_chapters):
    """Proportional 3-act boundaries (~25/50/25) valid for any chapter count.

    Returns ((act1_start, act1_end), (act2_start, act2_end),
    (act3_start, act3_end)), or None when the book is too short to have
    three distinguishable acts.
    """
    if total_chapters < 3:
        return None
    a1_end = max(1, round(total_chapters * 0.25))
    a2_end = max(a1_end + 1, round(total_chapters * 0.75))
    return ((1, a1_end), (a1_end + 1, a2_end), (a2_end + 1, total_chapters))


def verify_tonal_drift(roadmap_text, seed_concept, genre_name, total_chapters):
    """
    Evaluates Acts 2 & 3 of the roadmap for tonal drift or magic/tech rule breaks against Act 1.
    Returns (has_drift, feedback_message)
    """
    acts = _act_ranges(total_chapters)
    if acts is None:
        print(f"  INFO: {total_chapters} chapters — too short for act-based drift check, skipping.", file=sys.stderr)
        return False, ""
    (a1s, a1e), (a2s, a2e), (a3s, a3e) = acts

    print("Running Phase 1 tonal drift validation...", file=sys.stderr)
    prompt = f"""You are a master story editor. Your task is to analyze the proposed high-level roadmap of a novel for tonal drift, logic breaks, or sudden genre shifts.

    NOVEL GENRE: {genre_name}
    SEED CONCEPT:
    {seed_concept}

    HIGH-LEVEL ROADMAP:
    {roadmap_text}

    TASK:
    Analyze if Act 2 (Chapters {a2s}-{a2e}) or Act 3 (Chapters {a3s}-{a3e}) deviates significantly from the established world rules, tone, style, or stakes register of Act 1 (Chapters {a1s}-{a1e}).
    For example:
    - Does a political intrigue novel suddenly become a sci-fi simulation?
    - Does a grounded low-magic fantasy shift to high-fantasy multiversal travel with no setup?
    - Are the rules of the magic system or setting established in Act 1 violated later?

    Respond in JSON format:
    {{
      "has_drift": true/false,
      "analysis": "A detailed multi-sentence description of your analysis and findings.",
      "violations": [
        "Description of violation 1 (if any)...",
        "Description of violation 2 (if any)..."
      ]
    }}
    JSON only, no formatting/preamble outside the JSON object."""

    try:
        from core.llm import parse_json_response
        raw = call_llm(prompt=prompt, system="You are a meticulous book editor who outputs valid JSON only.", model_key="judge", max_tokens=2000, temperature=0.1)
        data = parse_json_response(raw)
        has_drift = data.get("has_drift", False)
        violations = data.get("violations", [])
        if has_drift and violations:
            feedback = "Tonal/Genre violations detected:\n" + "\n".join(f"- {v}" for v in violations)
            return True, feedback
        return False, ""
    except TruncationError:
        # A truncated judge verdict is UNKNOWN, not "no drift" — never fail open on truncation.
        raise
    except Exception as e:
        print(f"  WARN: Tonal drift validation call failed ({e}), skipping gatekeeper.", file=sys.stderr)
        return False, ""


def main():
    parser = argparse.ArgumentParser(description="Generate chapter outline block-by-block.")
    parser.add_argument("--retry-feedback", default="",
                        help="Error feedback from previous attempt (missing/out-of-order premise beats)")
    args = parser.parse_args()

    root = paths.get_root_dir()
    required = {
        "seed.txt": paths.get_seed_path(),
        "world.md": paths.get_world_path(),
        "characters.md": paths.get_characters_path(),
        "MYSTERY.md": root / "fuel" / "MYSTERY.md",
        "CRAFT.md": root / "fuel" / "CRAFT.md",
        "voice.md": paths.get_voice_path(),
    }
    for name, p in required.items():
        if not p.exists():
            print(f"ERROR: {name} not found at {p}", file=sys.stderr)
            sys.exit(1)

    seed = required["seed.txt"].read_text()
    world = required["world.md"].read_text()
    characters = required["characters.md"].read_text()
    mystery = required["MYSTERY.md"].read_text()
    craft = required["CRAFT.md"].read_text()
    voice = required["voice.md"].read_text()

    # Optional: sealed foundation (canon generated before outline when available).
    # Used only for plant-hygiene gates and action-plant guidance — never as prose.
    from core import canon as canon_mod
    from core import plant_hygiene
    canon_path = paths.get_canon_path()
    canon_text = canon_path.read_text(encoding="utf-8") if canon_path.exists() else ""
    parsed_canon = canon_mod.parse_canon(canon_text)
    reveal_chapter = parsed_canon.reveal_chapter()
    hygiene_denylist = canon_mod.sealed_denylist_terms(parsed_canon)
    plant_characters = canon_mod.action_plant_characters(parsed_canon, characters)
    plant_guidance = ""
    if reveal_chapter and reveal_chapter > 1:
        plant_list = ", ".join(plant_characters) if plant_characters else "(none auto-detected)"
        deny = ", ".join(hygiene_denylist[:20]) if hygiene_denylist else "(see canon)"
        plant_guidance = f"""
ACTION-SHAPED PLANTS (pre-reveal chapters 1-{reveal_chapter - 1}):
A major truth is sealed until chapter {reveal_chapter}. Before that chapter:
- Beats must be ACTION-SHAPED: what a character on the page can see/hear/do.
- Do NOT write meaning-shaped beats (who "secretly" is, who "really" controls what).
- Do NOT use these sealed terms in pre-reveal beats: {deny}
- These characters must appear as agents with observable action in multiple
  pre-reveal chapters (so a later retrofit has material): {plant_list}.
  Example action plant: "Mira arrives late, leaves a sealed letter on the table, does not explain."
"""
    
    # Extract voice part 2
    voice_lines = voice.split('\n')
    try:
        part2_start = next(i for i, l in enumerate(voice_lines) if 'Part 2' in l)
        voice_part2 = '\n'.join(voice_lines[part2_start:])
    except StopIteration:
        voice_part2 = voice

    genre_cfg = load_genre()
    genre_name = genre_cfg.get("genre_name", "Dark Political Fantasy")
    perspective = genre_cfg.get("perspective", "")
    perspective_line = ""
    if perspective == "first_person":
        perspective_line = ("MANDATORY PERSPECTIVE: The novel is FIRST-PERSON. Every chapter outline "
                            "must name a POV character and the prose will be narrated by them in 'I/me/my'.")
    elif perspective == "third_person":
        perspective_line = ("MANDATORY PERSPECTIVE: The novel is THIRD-PERSON (close limited). Every "
                            "chapter outline must name a POV character whose head the narration stays in.")
    try:
        state = json.loads((paths.get_project_dir() / "state.json").read_text(encoding="utf-8"))
        total_chapters = state.get("chapters_total", 30)
        title = state.get("title", "Untitled Novel")
    except Exception:
        total_chapters = genre_cfg.get("generation", {}).get("outline", {}).get("estimated_chapters", 30)
        title = "Untitled Novel"

    beats = genre_cfg.get("framework", {}).get("premise_arc_beats", [])
    numbered_beats = "\n".join(f"{i+1}. {b}" for i, b in enumerate(beats))

    # Calibrate scene-beat count to the per-chapter word budget: the outline
    # writer must not cram 6 fat beats into a 3000-word chapter — each beat is
    # roughly one scene (3-4 sentences), so cap beats by words_per_chapter.
    try:
        est_words = genre_cfg["generation"]["outline"]["estimated_words"]
        wpc = est_words // max(total_chapters, 1)
    except (KeyError, ZeroDivisionError):
        wpc = 3000
    beats_per_chapter = max(3, min(6, round(wpc / 650)))   # 3000w→5, 2400w→4, 4000w→6
    words_per_beat = max(250, wpc // beats_per_chapter)

    # Phase 1: High-Level Roadmap
    roadmap_path = paths.get_project_dir() / ".outline_roadmap.md"
    
    if args.retry_feedback and roadmap_path.exists():
        print("Retry detected: keeping existing high-level roadmap and regenerating Block 1.", file=sys.stderr)
        roadmap_content = roadmap_path.read_text(encoding="utf-8")
    else:
        print("Phase 1: Generating Global High-Level Roadmap...", file=sys.stderr)
        roadmap_prompt = f"""You are a master narrative architect. Your task is to generate a high-level roadmap and a Global Plot Threads Ledger for a novel titled "{title}" in the genre: {genre_name}.

SEED CONCEPT:
{seed}

WORLD BIBLE:
{world}

CHARACTER REGISTRY:
{characters}

VOICE STYLE GUIDE:
{voice_part2}

CRAFT GUIDELINES:
{craft}

{perspective_line}
TOTAL CHAPTERS: {total_chapters}
PREMISE ARC BEATS:
{numbered_beats}

TASK:
1. Create a high-level roadmap of the entire book. For each chapter from 1 to {total_chapters}, write a 1-to-2 sentence summary of the key event or beat in that chapter. You must distribute the premise arc beats logically across all chapters. Each chapter is {wpc} words — keep each chapter's roadmap summary focused on ONE main event plus at most one supporting thread, so the chapter can actually fit its word budget.
2. Create a "Global Plot Threads Ledger" listing 3 to 6 major plot threads, and specifying which chapters they are established (planted) and resolved (harvested). Use simple, lowercase slug identifiers for the threads (e.g. "silver_locket", "dead_king_secret").

FORMAT REQUIREMENT:
Your output must be structured markdown. Start the roadmap section with "## HIGH-LEVEL ROADMAP" and the ledger section with "## GLOBAL PLOT THREADS LEDGER".
Each chapter entry must start with "### Chapter N:".
"""
        # Roadmap generation is expensive on a thinking proxy. Tonel drift is a
        # quality signal, not a hard gate — after retries we keep the last
        # structurally valid roadmap instead of killing the whole foundation.
        roadmap_content = ""
        best_drift_feedback = ""
        max_roadmap_attempts = int(os.getenv("GESAKU_OUTLINE_ROADMAP_ATTEMPTS", "6"))
        for attempt in range(1, max_roadmap_attempts + 1):
            try:
                res = call_writer(roadmap_prompt)
            except TruncationError as e:
                print(f"  WARN: Roadmap attempt {attempt} truncated ({e}), retrying...", file=sys.stderr)
                continue
            except Exception as e:
                print(f"  WARN: Roadmap attempt {attempt} failed: {e}", file=sys.stderr)
                continue
            if "## HIGH-LEVEL ROADMAP" in res and "## GLOBAL PLOT THREADS LEDGER" in res:
                # Run the tonal drift check
                has_drift, feedback = verify_tonal_drift(res, seed, genre_name, total_chapters)
                if not has_drift:
                    roadmap_content = res
                    break
                else:
                    print(f"  WARN: Roadmap attempt {attempt} failed tonal drift check:\n{feedback}", file=sys.stderr)
                    best_drift_feedback = feedback
                    # Add drift feedback to prompt for self-correction
                    roadmap_prompt += f"\n\nERROR ON ATTEMPT {attempt}: {feedback}\nEnsure that the proposed outline maintains a consistent tone, stakes register, and world/magic rules between Act 1 and Acts 2/3."
                    if attempt == max_roadmap_attempts:
                        print("  WARN: accepting last structurally valid roadmap despite tonal drift (quality gate, not fatal).",
                              file=sys.stderr)
                        roadmap_content = res
            else:
                print(f"  WARN: Roadmap missing expected headers (## HIGH-LEVEL ROADMAP and/or ## GLOBAL PLOT THREADS LEDGER) on attempt {attempt}, retrying...", file=sys.stderr)
        if not roadmap_content:
            print("ERROR: Failed to generate valid roadmap.", file=sys.stderr)
            sys.exit(1)

        roadmap_path.write_text(roadmap_content, encoding="utf-8")

    # Phase 2: Block Expansion
    # Larger blocks (10) blew past local-proxy timeouts for a 24-chapter
    # thinking model. Smaller chunks finish and checkpoint outline.md so a
    # resume does not restart from chapter 1.
    block_size = int(os.getenv("GESAKU_OUTLINE_BLOCK_SIZE", "4"))
    blocks = []
    for start in range(1, total_chapters + 1, block_size):
        end = min(start + block_size - 1, total_chapters)
        blocks.append((start, end))

    detailed_outlines = {}
    
    # Load any already existing detailed outlines if we are resuming or retrying
    outline_path = paths.get_outline_path()
    if outline_path.exists():
        existing_text = outline_path.read_text(encoding="utf-8")
        # Extract existing chapters to see what we can keep (only from the Detailed section)
        if "## DETAILED CHAPTER OUTLINES" in existing_text:
            detailed_section = existing_text.split("## DETAILED CHAPTER OUTLINES", 1)[1]
            for ch in range(1, total_chapters + 1):
                pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{ch}\b.*?(?=###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*(?:\d+)\b|## Act|## Foreshadowing|$)'
                match = re.search(pattern, detailed_section, re.IGNORECASE | re.DOTALL)
                if match:
                    match_text = match.group(0).strip()
                    # Ensure it is a detailed outline, not a leftover snippet
                    if "POV:" in match_text or "Scene Beats:" in match_text:
                        detailed_outlines[ch] = match_text

    # If this is a retry, clear Block 1 (chapters 1-10) to force regeneration
    if args.retry_feedback:
        for ch in range(1, 11):
            if ch in detailed_outlines:
                del detailed_outlines[ch]

    # Expand each block
    for start, end in blocks:
        # Check if we already have ALL chapters in this block detailed
        if all(ch in detailed_outlines for ch in range(start, end + 1)):
            print(f"Block Ch {start}-{end} already expanded, skipping.", file=sys.stderr)
            continue

        print(f"Phase 2: Expanding Block Ch {start}-{end}...", file=sys.stderr)
        
        # Build previous chapters context (just the immediately preceding block for local continuity)
        prev_detailed = ""
        if start > 1:
            prev_start = max(1, start - block_size)
            prev_detailed = "\n\n".join(detailed_outlines[ch] for ch in range(prev_start, start) if ch in detailed_outlines)

        # Build active plants/debts context from previous blocks
        active_plants = []
        # Find all plants in previous chapters (tolerant of em dash / curly quotes)
        prev_text = "\n\n".join(detailed_outlines[ch] for ch in sorted(detailed_outlines.keys()) if ch < start)
        all_plants = re.findall(r'\[Plant:\s*([a-zA-Z0-9_]+)\s*[-–—]\s*["“]([^"”]+)["”]\]', prev_text)
        all_harvests = re.findall(r'\[Harvest:\s*([a-zA-Z0-9_]+)\s*[-–—]\s*["“]([^"”]+)["”]\]', prev_text)
        harvested_slugs = {h[0] for h in all_harvests}
        for slug, desc in all_plants:
            if slug not in harvested_slugs:
                active_plants.append(f"- [Plant: {slug} - \"{desc}\"]")
        active_plants_text = "\n".join(active_plants) if active_plants else "None (all previous plants resolved)"

        block_prompt = f"""You are a master story pacing engineer. Your task is to take the high-level roadmap and expand Chapters {start} through {end} of "{title}" into detailed chapter outlines.

GLOBAL ROADMAP & THREAD LEDGER:
{roadmap_content}

WORLD BIBLE:
{world}

CHARACTER REGISTRY:
{characters}

VOICE STYLE GUIDE:
{voice_part2}

{perspective_line}
ACTIVE PLANTS (open threads from previous chapters that need harvesting/resolution):
{active_plants_text}

PREVIOUS DETAILED OUTLINES (for local continuity):
{prev_detailed}

TASK:
Write the detailed outlines for Chapters {start} through {end}.
For EACH chapter in this range, you must output:
1. POV: [Character name]
2. Characters: [List of characters who appear in this chapter, comma-separated]
3. Emotional Arc: [Emotional shift, e.g. Contentment -> Dread]
4. Summary: [2-3 sentences of what happens]
5. Orientation Facts: [A bulleted list of 2-4 concrete, statable facts the outline commits to reveal/establish in this chapter for orientation, e.g. relationships, setting details, background context. Especially critical for Chapter 1 and character introduction chapters]
6. Scene Stakes: [One sentence describing what concrete external stakes are at play or could change by the end of this specific chapter]
7. Scene Beats: A numbered list of EXACTLY {beats_per_chapter} sequential scene beats (no more, no less). Each beat MUST have a detailed paragraph (3-4 sentences) describing the events. Budget each beat to roughly {words_per_beat} words of prose — the whole chapter is only {wpc} words, so keep the beat count and per-beat depth matched to the word budget. Do not add extra beats beyond {beats_per_chapter}; if the story needs more, make the beats denser instead.
8. Plants & Harvests: List of plants and harvests, tagged exactly as `[Plant: slug_name - "Description"]` or `[Harvest: slug_name - "Description"]`.

CRITICAL RULES:
- Use standard slug identifiers matching the Global Plot Threads Ledger where applicable (e.g. silver_locket, dead_king_secret).
- Generate exactly Chapters {start} through {end}. Do not skip any chapters. Do not write outlines for chapters outside this range.
- Start each chapter outline with a header: "### Chapter N: [Title]"
- If you are writing Chapter 1, it MUST include a "PREMISE BEATS" section with a bullet line per premise arc beat, in this exact format:
    PREMISE BEATS:
    - beat_label: scene description
  using these beat labels IN ORDER: {numbered_beats}
  Each bullet's scene description is 1-2 sentences. Put this section right after the "Emotional Arc" line and before the other fields. Then continue with the remaining fields (Summary, Scene Stakes, Scene Beats, Plants & Harvests).
{plant_guidance}
"""
        # Append retry feedback if editing Block 1
        if start == 1 and args.retry_feedback:
            block_prompt += f"\n\nYOUR PREVIOUS ATTEMPT FOR CHAPTER 1 HAD THESE ERRORS:\n{args.retry_feedback}\nMake sure Chapter 1 includes the PREMISE BEATS section in correct format."

        block_result = ""
        last_err = ""
        for attempt in range(1, 4):
            try:
                res = call_writer(block_prompt)
            except TruncationError as e:
                last_err = f"truncated: {e}"
                print(f"  WARN: Block Ch {start}-{end} attempt {attempt} truncated ({e}), retrying...", file=sys.stderr)
                continue
            except Exception as e:
                last_err = str(e)
                print(f"  WARN: Block Ch {start}-{end} attempt {attempt} failed: {e}", file=sys.stderr)
                continue
            passed, err = validate_block_output(res, start, end)
            if passed and reveal_chapter and reveal_chapter > 1:
                leaks = plant_hygiene.check_pre_reveal_leaks(
                    res, hygiene_denylist, reveal_chapter
                )
                if leaks:
                    passed = False
                    err = "plant-hygiene: " + "; ".join(leaks[:8])
            if passed:
                block_result = res
                break
            last_err = err
            print(f"  WARN: Block Ch {start}-{end} validation failed on attempt {attempt}/3: {err}. Retrying...", file=sys.stderr)
            block_prompt += f"\n\nERROR ON ATTEMPT {attempt}: {err}\nEnsure you write detailed outlines for all chapters from {start} to {end}."

        if not block_result:
            # Partial outline.md is better than a silent missing file: the
            # caller can resume and skip already-expanded blocks.
            if detailed_outlines:
                full_outline_text = f"# {title.upper()}\n\n" + roadmap_content + "\n\n## DETAILED CHAPTER OUTLINES\n\n" + \
                                    "\n\n---\n\n".join(detailed_outlines[ch] for ch in sorted(detailed_outlines.keys()))
                outline_path.write_text(full_outline_text, encoding="utf-8")
                print(f"  (saved partial outline.md with {len(detailed_outlines)} chapters before exit)",
                      file=sys.stderr)
            print(f"ERROR: Failed to expand Block Ch {start}-{end} after 3 attempts: {last_err}", file=sys.stderr)
            sys.exit(1)

        # Parse and save the block chapters to detailed_outlines
        for ch in range(start, end + 1):
            pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{ch}\b.*?(?=###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*(?:\d+)\b|## Act|## Foreshadowing|$)'
            match = re.search(pattern, block_result, re.IGNORECASE | re.DOTALL)
            if match:
                detailed_outlines[ch] = match.group(0).strip()
            else:
                print(f"ERROR: Could not isolate Chapter {ch} outline from block output.", file=sys.stderr)
                sys.exit(1)

        # Save active block progress in outline.md immediately
        full_outline_text = f"# {title.upper()}\n\n" + roadmap_content + "\n\n## DETAILED CHAPTER OUTLINES\n\n" + \
                            "\n\n---\n\n".join(detailed_outlines[ch] for ch in sorted(detailed_outlines.keys()))
        outline_path.write_text(full_outline_text, encoding="utf-8")

    # Late-Introduction Validator
    print("Running late-introduction validator...", file=sys.stderr)
    late_intro_errors = []
    character_first_appearance = {}
    character_block_appearances = {}
    
    for ch in sorted(detailed_outlines.keys()):
        ch_text = detailed_outlines[ch]
        chars_match = re.search(r'-\s*(?:\*\*|\*)?Characters(?:\*\*|\*)?:\s*(.*)', ch_text, re.IGNORECASE)
        if chars_match:
            char_line = chars_match.group(1).strip()
            char_line = re.sub(r'[\*\_\-\[\]\(\)]', '', char_line)
            chars = [c.strip() for c in char_line.split(',') if c.strip()]
            for char in chars:
                char_lower = char.lower()
                # Skip generic tokens
                if char_lower in ["unseen", "mentioned", "referenced", "none"]:
                    continue
                # Clean up role annotations (e.g. "Kael (Spare)" -> "kael")
                char_clean = re.sub(r'\s*\(.*?\)', '', char_lower).strip()
                if not char_clean:
                    continue
                if char_clean not in character_first_appearance:
                    character_first_appearance[char_clean] = (ch, char.strip())
                if char_clean not in character_block_appearances:
                    character_block_appearances[char_clean] = []
                character_block_appearances[char_clean].append(ch)

    cutoff_chapter = int(total_chapters * 0.6)
    for char_clean, (first_ch, char_name) in character_first_appearance.items():
        if first_ch > cutoff_chapter:
            appearances = character_block_appearances[char_clean]
            if len(appearances) >= 3:
                late_intro_errors.append(
                    f"Character '{char_name}' is introduced late in Ch {first_ch} (after 60% mark) "
                    f"but appears in {len(appearances)} chapters: {appearances}."
                )

    if late_intro_errors:
        print("\n[WARN] Late-introduction validator flagged structural risks:", file=sys.stderr)
        for err in late_intro_errors:
            print(f"  - {err}", file=sys.stderr)
        print("Check outline.md for late-introduction issues. Continuing pipeline...\n", file=sys.stderr)

    # Final assembly and save
    full_outline_text = f"# {title.upper()}\n\n" + roadmap_content + "\n\n## DETAILED CHAPTER OUTLINES\n\n" + \
                        "\n\n---\n\n".join(detailed_outlines[ch] for ch in sorted(detailed_outlines.keys()))
    outline_path.write_text(full_outline_text, encoding="utf-8")

    # Plant hygiene sidecar (leaks + coverage). Failures warn here; run_pipeline
    # re-checks after foundation and can regenerate.
    if reveal_chapter and reveal_chapter > 1:
        hy_ok, hy_err, hy_side = plant_hygiene.validate_outline_plant_hygiene(
            full_outline_text, canon_text, characters
        )
        side_path = paths.get_project_dir() / "plant_hygiene.json"
        side_path.write_text(json.dumps(hy_side, indent=2), encoding="utf-8")
        if not hy_ok:
            print(f"[WARN] Outline plant hygiene failed: {hy_err}", file=sys.stderr)
            print("See plant_hygiene.json — run_pipeline will gate on this.", file=sys.stderr)
        else:
            print(
                f"Plant hygiene OK (reveal ch{reveal_chapter}, "
                f"plants={hy_side.get('action_plant_characters')})",
                file=sys.stderr,
            )

    # Save a copy as .outline_part1.md for backwards compatibility
    (paths.get_project_dir() / ".outline_part1.md").write_text(full_outline_text, encoding="utf-8")

    print("Outline generation complete!", file=sys.stderr)

if __name__ == "__main__":
    main()