#!/usr/bin/env python3
"""
gen_genre_framework.py — Step 0: Initialize genre configuration.
Reads genre description + chapter count + user notes, calls LLM with meta-prompt,
validates output, writes active_genre.json.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm, extract_text_from_response, get_max_tokens_with_thinking
from core.paths import load_prompt
from core import paths
import os
import re
import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Load env
from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")


# Shared system prompt (handles identity and mapping/translation table)
SYSTEM_PROMPT = load_prompt("genre_framework_system")


PASS1_META_PROMPT = load_prompt("genre_framework_pass1")
PASS2_META_PROMPT = load_prompt("genre_framework_pass2")


def strip_json_fences(text):
    """Strip markdown code fences if present."""
    text = text.strip()
    match = re.match(r'^```(?:json)?\s*\n(.*?)\n```\s*$', text, re.DOTALL)
    return match.group(1).strip() if match else text


REQUIRED_PLACEHOLDERS = {
    "gen_world_prompt":          ["{seed}", "{voice_part2}"],
    "gen_characters_prompt":     ["{seed}", "{world}", "{voice_part2}"],
    "gen_outline_prompt":        ["{seed}", "{world}", "{characters}", "{voice_part2}"],
    "gen_outline_part2_prompt":  ["{part1}"],
    "gen_canon_prompt":          ["{seed}", "{world}", "{characters}"],
    "gen_chapter_title_rewriter_prompt": ["{outline}", "{seed}"],
}


def validate_placeholders(config):
    """Check that generated prompt strings contain their required placeholders."""
    errors = []
    generation = config.get("generation", {})
    for field, required in REQUIRED_PLACEHOLDERS.items():
        prompt_str = generation.get(field, "")
        for placeholder in required:
            # LLM writes {{seed}} (double-brace), which becomes {seed} after escaping
            if placeholder not in prompt_str and placeholder.replace("{", "{{").replace("}", "}}") not in prompt_str:
                errors.append(f"generation.{field} is missing required placeholder '{placeholder}'")
    return errors


def validate_output(config):
    """Basic validation before writing. Returns list of errors."""
    from core.genre import validate as full_validate
    errors = []
    try:
        full_validate(config)
    except ValueError as e:
        errors.append(str(e))
    errors.extend(validate_placeholders(config))
    return errors


# Deterministic last-resort repair: if the writer model drops a required
# placeholder from a template, append a labeled block for it instead of
# failing the whole foundation phase. The appended block is exactly what
# the prompt rules asked the model to weave in anyway.
PLACEHOLDER_REPAIR_BLOCKS = {
    "{seed}": "\n\nSEED CONCEPT:\n{seed}",
    "{world}": "\n\nWORLD BIBLE:\n{world}",
    "{characters}": "\n\nCHARACTER REGISTRY:\n{characters}",
    "{voice_part2}": "\n\nVOICE STYLE GUIDE (follow this voice strictly):\n{voice_part2}",
    "{outline}": "\n\nCURRENT OUTLINE:\n{outline}",
    "{part1}": "\n\nPART 1:\n{part1}",
    "{premise_arc_beats}": "\n\nREQUIRED PREMISE ARC BEATS (in order):\n{premise_arc_beats}",
}


def repair_missing_placeholders(config):
    """Append missing required placeholders to generation templates.

    Returns a list of human-readable repairs applied. Only touches fields
    that already exist; structural problems are left for validate_output.
    """
    applied = []
    generation = config.get("generation", {})
    for field, required in REQUIRED_PLACEHOLDERS.items():
        prompt_str = generation.get(field)
        if not isinstance(prompt_str, str):
            continue
        for ph in required:
            doubled = ph.replace("{", "{{").replace("}", "}}")
            if ph not in prompt_str and doubled not in prompt_str:
                block = PLACEHOLDER_REPAIR_BLOCKS.get(ph)
                if block is None:
                    continue
                generation[field] = prompt_str + block
                prompt_str = generation[field]
                applied.append(f"{field}: auto-appended {ph}")
    return applied


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Initialize genre configuration for the novel pipeline")
    parser.add_argument("--genre", default=os.environ.get("GESAKU_GENRE", ""),
                        help="Genre description (e.g., 'Cyberpunk Noir', 'High School Romance')")
    parser.add_argument("--chapters", default=os.environ.get("GESAKU_CHAPTERS", "24"),
                        help="Number of chapters (or 'short story', 'novella', 'epic 40-chapter saga')")
    parser.add_argument("--words-per-chapter", type=int, default=3200,
                        help="Target word count per chapter (default: 3200)")
    parser.add_argument("--perspective", default="", choices=["", "first_person", "third_person"],
                        help="Force narrative perspective (first_person / third_person). Empty = let foundation decide.")
    parser.add_argument("--prose-mode", default=os.environ.get("GESAKU_PROSE_MODE", ""),
                        choices=["", "first_intimate", "first_voicey", "third_close", "third_scene"],
                        help="Prose distance/rhythm pack. Empty = no pack (foundation defaults).")
    parser.add_argument("--notes", default=os.environ.get("GESAKU_NOTES", ""),
                        help="User's specific ideas: character names, plot twists, Chekhov's guns")
    args = parser.parse_args()

    if not args.genre:
        print("ERROR: No genre specified. Use --genre or set GESAKU_GENRE env var.", file=sys.stderr)
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY", ""):
        print("ERROR: Set ANTHROPIC_API_KEY in .env first", file=sys.stderr)
        sys.exit(1)

    # Parse chapter count
    chapter_count_raw = args.chapters.strip().lower()
    try:
        chapter_count = int(chapter_count_raw)
    except ValueError:
        # Parse descriptive length
        if "short" in chapter_count_raw or "story" in chapter_count_raw:
            chapter_count = 8
        elif "novella" in chapter_count_raw or "novelette" in chapter_count_raw:
            chapter_count = 14
        elif "epic" in chapter_count_raw or "saga" in chapter_count_raw:
            # Try to extract number
            import re as re2
            nums = re2.findall(r'\d+', chapter_count_raw)
            chapter_count = int(nums[0]) if nums else 40
        else:
            print(f"WARNING: Could not parse chapter count '{args.chapters}', defaulting to 24", file=sys.stderr)
            chapter_count = 24

    estimated_words = chapter_count * args.words_per_chapter
    beats_per_chapter = max(3, min(6, round(args.words_per_chapter / 650)))
    words_per_beat = max(250, args.words_per_chapter // beats_per_chapter)

    # Build user directives block
    if args.notes:
        user_block = f"\n=== USER DIRECTIVES ===\nThe user has provided specific ideas for this novel:\n{args.notes}\n\nYou MUST incorporate these into the generation descriptions, character focus_areas, and outline.notes so the pipeline preserves them."
        user_field = args.notes
    else:
        user_block = ""
        user_field = ""

    perspective_directive = ""
    if args.perspective:
        if args.perspective == "first_person":
            perspective_directive = (
                "\n=== MANDATORY PERSPECTIVE ===\n"
                "The novel MUST be written in FIRST-PERSON narration. All chapters are narrated "
                "by the POV character using 'I/me/my', in close first-person limited. The outline "
                "writer must assign a POV character per chapter and the draft instructions must "
                "require strict first-person. Never use third-person narration in any chapter."
            )
        else:
            perspective_directive = (
                "\n=== MANDATORY PERSPECTIVE ===\n"
                "The novel MUST be written in THIRD-PERSON narration. All chapters are narrated in "
                "close third-person limited, anchored to the assigned POV character ('he/she/they', "
                "character name). The outline writer must assign a POV character per chapter and the "
                "draft instructions must require strict third-person. Never switch to first-person."
            )

    prose_mode_directive = ""
    if args.prose_mode:
        from core.genre import load_prose_pack
        pack = load_prose_pack(args.prose_mode)
        prose_mode_directive = (
            f"\n=== PROSE MODE ({args.prose_mode}) ===\n"
            f"Bake this prose-distance pack into identity.chapter_system, "
            f"generation.draft_chapter_instructions, generation.anti_pattern_rules, "
            f"and evaluation.chapter so drafting and scoring enforce it.\n\n{pack}\n"
        )

    print(f"Generating genre config for: {args.genre} ({chapter_count} chapters, {estimated_words:,} words)...", file=sys.stderr)
    if args.notes:
        print(f"User notes: {args.notes}", file=sys.stderr)

    # ==========================================
    # PASS 1: Generate structural config
    # ==========================================
    pass1_prompt = PASS1_META_PROMPT.format(
        genre_description=args.genre,
        chapter_count=chapter_count,
        estimated_words=estimated_words,
        words_per_chapter=args.words_per_chapter,
        beats_per_chapter=beats_per_chapter,
        words_per_beat=words_per_beat,
        user_directives_block=user_block + perspective_directive + prose_mode_directive
    )

    config1 = None
    print(f"Executing Pass 1 (Structural Design)...", file=sys.stderr)
    for attempt in range(3):
        print(f"  Pass 1 Attempt {attempt + 1}...", file=sys.stderr)
        raw1 = call_llm(
            prompt=pass1_prompt,
            system=SYSTEM_PROMPT,
            model_key="judge",  # Premium model for Pass 1
            max_tokens=16000,
            temperature=0.7,
            timeout_role="standard"
        )
        cleaned1 = strip_json_fences(raw1)
        try:
            config1 = json.loads(cleaned1)
            
            # Structural validate
            errors = []
            for key in ["genre_name", "identity", "evaluation", "framework"]:
                if key not in config1:
                    errors.append(f"Missing top-level key: {key}")
            if errors:
                raise ValueError("Structural keys missing: " + ", ".join(errors))
            
            # Temporary full validate check with mock generation block
            temp_config = dict(config1)
            temp_config["generation"] = {
                "world": {"description": "temp world building bible description", "sections": []},
                "character": {"description": "temp character description", "focus_areas": []},
                "outline": {"description": "temp outline", "estimated_chapters": chapter_count, "estimated_words": estimated_words, "notes": []},
                "seed_generate_prompt": "temp template with at least fifty characters in length to pass validations",
                "seed_riff_prompt": "temp template with at least fifty characters in length to pass validations",
                "gen_world_prompt": "temp template with at least fifty characters in length to pass validations",
                "gen_characters_prompt": "temp template with at least fifty characters in length to pass validations",
                "gen_outline_prompt": "temp template with at least fifty characters in length to pass validations",
                "gen_outline_part2_prompt": "temp template with at least fifty characters in length to pass validations",
                "gen_canon_prompt": "temp template with at least fifty characters in length to pass validations",
                "gen_chapter_title_rewriter_prompt": "temp template with at least fifty characters in length to pass validations",
                "draft_chapter_instructions": "temp template with at least fifty characters in length to pass validations",
                "anti_pattern_rules": "temp template with at least fifty characters in length to pass validations",
                "canon_categories": [],
                "arc_summary_premise": "temp template with at least fifty characters in length to pass validations"
            }
            from core.genre import validate as full_validate
            full_validate(temp_config)
            
            print("  Pass 1 successful.", file=sys.stderr)
            break
        except Exception as e:
            print(f"  Pass 1 error: {e}", file=sys.stderr)
            if attempt < 2:
                # Add validation error feedback
                pass1_prompt += f"\n\n=== FEEDBACK (attempt {attempt + 1}) ===\nThe previous output had this error: {e}\nPlease fix this and output valid JSON matching the schema rules."
            else:
                print(f"  Failed after 3 attempts. Raw output saved to {BASE_DIR / 'genre_fail.json'}", file=sys.stderr)
                (BASE_DIR / "genre_fail.json").write_text(raw1)
                sys.exit(1)

    # ==========================================
    # PASS 2: Generate prompts & generation config
    # ==========================================
    pass2_prompt = PASS2_META_PROMPT.format(
        genre_config=json.dumps(config1, indent=2),
        genre_description=args.genre,
        chapter_count=chapter_count,
        estimated_words=estimated_words,
        words_per_chapter=args.words_per_chapter,
        beats_per_chapter=beats_per_chapter,
        words_per_beat=words_per_beat,
        user_directives_block=user_block + perspective_directive + prose_mode_directive
    )

    config2 = None
    print(f"Executing Pass 2 (Content Generation & Prompts)...", file=sys.stderr)
    for attempt in range(3):
        print(f"  Pass 2 Attempt {attempt + 1}...", file=sys.stderr)
        raw2 = call_llm(
            prompt=pass2_prompt,
            system=SYSTEM_PROMPT,
            model_key="writer",  # Creative writer model for prompt templates
            max_tokens=16000,
            temperature=0.7,
            timeout_role="standard"
        )
        cleaned2 = strip_json_fences(raw2)
        try:
            config2 = json.loads(cleaned2)
            
            # Explicit key check for generation
            if "generation" not in config2:
                raise ValueError("Pass 2 output missing top-level 'generation' key")
            
            # Merge Pass 2 generation into Pass 1 structure
            merged_config = dict(config1)
            merged_config["generation"] = config2["generation"]
            merged_config["user_directives"] = user_field
            merged_config["perspective"] = args.perspective or ""
            merged_config["prose_mode"] = args.prose_mode or ""

            # Correct chapter counts and estimated words
            if "outline" in merged_config["generation"]:
                merged_config["generation"]["outline"]["estimated_chapters"] = chapter_count
                merged_config["generation"]["outline"]["estimated_words"] = estimated_words

            # Run full validation including placeholder checks. Missing
            # placeholders are repaired deterministically instead of burning
            # a retry — the model dropping {voice_part2} 3/3 times at
            # temperature 0.7 killed whole runs (observed in smoke test).
            errors = validate_output(merged_config)
            if errors:
                repairs = repair_missing_placeholders(merged_config)
                if repairs:
                    for r in repairs:
                        print(f"  Pass 2 auto-repair: {r}", file=sys.stderr)
                    errors = validate_output(merged_config)
            if errors:
                raise ValueError("\n".join(errors))

            config1 = merged_config
            print("  Pass 2 successful.", file=sys.stderr)
            break
        except Exception as e:
            print(f"  Pass 2 error: {e}", file=sys.stderr)
            if attempt < 2:
                # Add validation error feedback
                pass2_prompt += f"\n\n=== FEEDBACK (attempt {attempt + 1}) ===\nThe previous output had these errors: {e}\nPlease fix these and output valid JSON matching the schema rules."
            else:
                print(f"  Failed after 3 attempts. Raw output saved to {BASE_DIR / 'genre_fail.json'}", file=sys.stderr)
                (BASE_DIR / "genre_fail.json").write_text(raw2)
                sys.exit(1)

    # Success — write active_genre.json
    out_path = paths.get_active_genre_path()
    paths.save_json_atomic(config1, out_path)
    print(f"✅ Genre config written to {out_path}", file=sys.stderr)
    print(f"   Genre: {config1['genre_name']}")
    print(f"   Chapters: {chapter_count}, ~{estimated_words:,} words")
    print(f"   Foundation dims: {[d['key'] for d in config1['evaluation']['foundation']['dimensions']]}")
    print(f"   Chapter dims: {[d['key'] for d in config1['evaluation']['chapter']['dimensions']]}")


if __name__ == "__main__":
    main()
