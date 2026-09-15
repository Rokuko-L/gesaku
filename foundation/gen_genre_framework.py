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


PASS1_META_PROMPT = """=== USER INPUT ===
Genre: {genre_description}
Target length: {chapter_count} chapters (~{estimated_words} words, ~{words_per_chapter} words/chapter)
{user_directives_block}

=== YOUR TASK ===
Generate the structural genre configuration as valid JSON. Do not write any generation prompt templates, focus areas, sections, or instructions. Generate only these fields:

1. "genre_name": "str — name of the genre"
2. "identity": {{
     "seed_system": "str — system prompt for seed generator",
     "world_system": "str — system prompt for world bible generator",
     "character_system": "str — system prompt for character designer",
     "outline_system": "str — system prompt for outline generator",
     "chapter_system": "str — system prompt for chapter drafter",
     "revision_system": "str — system prompt for revision writer",
     "canon_system": "str — system prompt for canon extractor",
     "evaluator_system": "str — must start with 'You are a literary critic and novel editor.'"
   }}
3. "evaluation": {{
     "foundation": {{
       "overall_calibration": "str — overall foundation calibration",
       "dimensions": [
         {{"key": "world_depth", "weight": 0.25, "criteria": "str"}},
         {{"key": "character_depth", "weight": 0.25, "criteria": "str"}},
         {{"key": "plot_structure", "weight": 0.15, "criteria": "str"}},
         {{"key": "internal_consistency", "weight": 0.1, "criteria": "str"}},
         {{"key": "voice_clarity", "weight": 0.15, "criteria": "str"}},
         {{"key": "canon_coverage", "weight": 0.1, "criteria": "str"}}
       ]
     }},
      "chapter": {{
        "overall_calibration": "str — overall chapter calibration",
        "dimensions": [
          {{"key": "voice_adherence", "weight": 0.15, "criteria": "str"}},
          {{"key": "beat_coverage", "weight": 0.125, "criteria": "str"}},
          {{"key": "character_voice", "weight": 0.175, "criteria": "str"}},
          {{"key": "prose_quality", "weight": 0.15, "criteria": "str"}},
          {{"key": "engagement", "weight": 0.15, "criteria": "str"}},
          {{"key": "continuity", "weight": 0.1, "criteria": "str"}},
          {{"key": "reader_grounding", "weight": 0.15, "criteria": "str — evaluate whether this chapter assumes knowledge that has not yet been put on the page, or uses names/titles/terms without introducing them. Low score = the chapter expects the reader to know something that canon-through-previous-chapters doesn't establish. Cite the specific offending phrase."}}
        ]
      }},
      "reader_panel": {{
        "genre_reader_identity": "str — system prompt for reader panel",
        "prompt_modifications": {{
          "earned_ending_hint": "str",
          "extra_questions": {{}}
        }},
        "title_judges": [
          {{
            "key": "editor",
            "name": "The Editor",
            "persona": "str — 2-3 sentence persona for title evaluation"
          }},
          {{
            "key": "genre_reader",
            "name": "The Genre Reader",
            "persona": "str — 2-3 sentence persona for title evaluation"
          }},
          {{
            "key": "writer",
            "name": "The Writer",
            "persona": "str — 2-3 sentence persona for title evaluation"
          }},
          {{
            "key": "first_reader",
            "name": "The First Reader",
            "persona": "str — 2-3 sentence persona for title evaluation"
          }}
        ]
      }}
   }}
4. "framework": {{
      "lore_priorities": "str",
      "stability_trap_applies": true,
      "character_framework": "str",
      "plot_framework": "str",
      "disclosure_framework": "str — per-genre description of how this genre orients new readers. Examples: 'drops readers into the middle and backfills through context'; 'slow, deliberate setup with heavy orientation beats in ch1-3'; 'genre-savvy opening that assumes reader knows the tropes and plays with subversion immediately'; 'procedural, establishes physical and social rules before introducing conflict'. State how many chapters typically pass before the reader has a complete picture of the world/genre premise.",
      "premise_arc": "str — narrative description of how premise is established before the main plot begins. Example for isekai: 'cold open in the game/ordinary world — reader attaches to the setting first; then reveals the MC as an observer commenting on it; inciting incident (isekai/reincarnation); arrival in the new world; reaction and rules exposition; THEN chapter 1 proper.' Example for mystery: 'open on the discovery (the body, the crime scene); establish the investigator's presence and relationship to the event; backfill just enough context to make the investigation matter; then proceed.'",
      "premise_arc_beats": [
        "str — snake_case beat labels in required order for chapter 1's premise-establishment phase. 3-6 beats. Each beat is one required scene-slot in the outline. Examples: Isekai: ['ordinary_world', 'observer_reveal', 'inciting_incident', 'arrival', 'reaction_rules']; Mystery: ['discovery', 'investigator_intro', 'context_backfill']; Political comedy: ['power_dynamic', 'inciting_disruption', 'flawed_response', 'consequences']; Literary: ['ordinary_world', 'crack', 'descent', 'new_equilibrium']."
      ]
    }}

=== RULES ===
- Dimension KEYS in evaluation are FIXED (world_depth, character_depth, plot_structure, internal_consistency, voice_clarity, canon_coverage, and voice_adherence, beat_coverage, character_voice, prose_quality, engagement, continuity, reader_grounding).
- Dimension weights must sum to 1.0 (allow ±0.02).
- Criteria strings must be specific and actionable (30+ characters).
- evaluator_system must start with "You are a literary critic and novel editor."
"""


PASS2_META_PROMPT = """=== STRUCTURAL CONFIGURATION ===
{genre_config}

=== USER INPUT ===
Genre: {genre_description}
Target length: {chapter_count} chapters (~{estimated_words} words, ~{words_per_chapter} words/chapter)
{user_directives_block}

=== YOUR TASK ===
Generate the complete content generation configuration block ("generation") as valid JSON, including prompts, world bibles structure, character registry structure, and chapter instructions. You must generate:

1. "generation": {{
     "world": {{ "description": "...", "sections": ["list of section headers"] }},
     "character": {{ "description": "...", "focus_areas": ["list of focus areas"] }},
     "outline": {{ "description": "...", "estimated_chapters": {chapter_count}, "estimated_words": {estimated_words}, "notes": ["structural notes"] }},
     "seed_generate_prompt": "...",
     "seed_riff_prompt": "...",
     "gen_world_prompt": "...",
     "gen_characters_prompt": "...",
     "gen_outline_prompt": "...",
     "gen_outline_part2_prompt": "...",
     "gen_canon_prompt": "Extract baseline canon facts from the seed {{seed}}, world bible {{world}}, and character registry {{characters}} — these are true from the start of the story but have not yet been revealed to any reader. Output structured facts only; do not repeat world-building verbatim.",
     "gen_chapter_title_rewriter_prompt": "...",
     "draft_chapter_instructions": "...",
     "anti_pattern_rules": "...",
     "canon_categories": ["list of category headers"],
     "arc_summary_premise": "..."
   }}

=== RULES ===
- Prompt strings must be substantial (100+ characters).
- Prompts MUST use the template parameters as required (using either single braces like {{placeholder}} or double braces like {{{{placeholder}}}}). Specifically:
  * "gen_world_prompt" MUST contain: {{seed}} AND {{voice_part2}}
  * "gen_characters_prompt" MUST contain: {{seed}} AND {{world}} AND {{voice_part2}}
  * "gen_outline_prompt" MUST contain: {{seed}} AND {{world}} AND {{characters}} AND {{voice_part2}} AND {{premise_arc_beats}}
  * "gen_outline_part2_prompt" MUST contain: {{part1}}
  * "gen_canon_prompt" MUST contain: {{seed}} AND {{world}} AND {{characters}}. Frame it as extracting baseline canon facts from the seed/world/characters themselves (not from a chapter), marking them as "true from the start but not yet revealed to readers."
  * "gen_chapter_title_rewriter_prompt" MUST contain: {{outline}} AND {{seed}}.
    - This prompt will guide the LLM to rewrite the chapter titles in the outline (`outline.md`) to make them catchy, witty, and genre-appropriate while preventing repetitive title beginnings.
    - It must instruct the model to return a raw JSON object only, mapping Chapter Numbers (as strings or integers) to their new, polished Chapter Titles (e.g., `{{"1": "New Title 1", "2": "New Title 2"}}`).
    - It must instruct the model to limit titles starting with "The" to at most 30%, and titles starting with "In Which" to at most 10%, ensuring a diverse range of starting words.
- "gen_outline_prompt" and "gen_outline_part2_prompt" MUST explicitly instruct the outline writer to generate a unique, evocative, and thematic chapter title for every single chapter (e.g., in the format "Chapter N: Title") instead of using generic titles like "Chapter N".
  * Recommend style guidelines and examples for chapter titles that match the tone/genre of the novel. Show that chapter titles can be:
    - Witty, self-aware meta-commentary (e.g., "In Which Things Go Wrong Immediately")
    - Character-driven recaps (e.g., "Dinner Party Disaster")
    - Stylistic logs (e.g., a diary record, or at most ONE system diagnostic log like 'system_diagnostic_failed' if the genre features technology/systems, but do not mix system log names into general examples of normal title beginnings).
    Give the writer model the flexibility to choose or blend these styles creatively.
    * CRITICAL: Vary your chapter title beginnings — do not start every title with "The" or "A" / "An". Instruct the model that NO MORE than 30% of the chapters should start with the word "The". Show alternative structural formats (e.g., gerunds like "Gaslighting the Inquisition", direct questions, prepositional starters, or starting directly with nouns/characters).
- "gen_outline_prompt" and "gen_outline_part2_prompt" MUST explicitly instruct the outline writer to generate a detailed, structured entry for every single chapter in the outline. For each chapter, the outline MUST include: (1) POV characters, (2) Emotional arc, (3) A brief summary, (4) A list of scene beats CALIBRATED TO THE WORD BUDGET, and (5) Specific plants and harvests. This high level of detail is mandatory to ensure the chapter drafting model has enough pacing material to write a full chapter of approximately {words_per_chapter} words.
- The scene beat count MUST be matched to the word budget: ~1 beat per 600-700 words of chapter prose, at most 6 beats, at least 3. For {words_per_chapter}-word chapters that is {beats_per_chapter} beats. Instruct the outline writer to write EXACTLY {beats_per_chapter} scene beats per chapter (never more — a chapter that crams more beats than its word budget can carry forces the drafter to compress, and compression is what produces staccato AI-slop prose). Each beat should be budgeted at roughly {words_per_beat} words.
- "gen_outline_prompt" MUST instruct the outline writer that Chapter 1's entry MUST include a parseable "PREMISE BEATS" section containing one bullet per beat from the premise_arc_beats list (passed as {{premise_arc_beats}}). Required format:
    PREMISE BEATS:
    - {{beat_label}}: {{scene summary}}
    - {{beat_label}}: {{scene summary}}
    ...
  All beats must appear, in order. Each beat gets a real scene description (not a single sentence — a real summary of what happens in that scene). After the premise beats, include a "MAIN PLOT:" section for Chapter 1's post-premise content.
  Also instruct that Chapter 1 must be comprehensible to a reader who knows NOTHING about the story. Every location, person, and concept must be introduced through the chapter's events — not assumed. The inciting incident should NOT occur in the first premise beat; the reader needs to orient to the ordinary world first.
- "draft_chapter_instructions" MUST instruct the writer to start the chapter markdown file with a top-level header including both the chapter number and the specific title from the outline (e.g., "# Chapter N: [Title]").
- "draft_chapter_instructions" must weave in a firm requirement that each chapter is approximately {words_per_chapter} words.
- "draft_chapter_instructions" MUST instruct the chapter writer: Chapter 1's outline contains a "PREMISE BEATS" section. The writer MUST draft prose for each beat in order before moving to the MAIN PLOT scenes. Each beat gets real scene treatment — do not compress or skip beats. The reader knows nothing about this world at the start of Chapter 1.
- "draft_chapter_instructions" must NOT include guidance that encourages short or staccato sentence patterns (e.g. "use short sentences" or "snappy dialogue"). A separate structural guardrail enforces sentence-length variety; the genre instructions should not contradict it.
- The "framework" section from PASS1 contains "disclosure_framework" — a per-genre description of how this genre orients new readers. You MUST thread this into:
    * "gen_outline_prompt": instruct the outline writer to schedule heavier setup/orientation beats early when the genre calls for it, and to pace revelation according to the disclosure convention.
    * "draft_chapter_instructions": instruct the chapter writer about the expected pacing of reader orientation and disclosure for this genre.
- The "framework" section from PASS1 contains "premise_arc_beats" — a list of required beat labels in order for chapter 1's premise-establishment phase. Format {{premise_arc_beats}} as a numbered list when injecting into the gen_outline_prompt template (so the writer model sees "1. beat_name\n2. beat_name\n..." instead of a raw list or comma-separated string).
- "anti_pattern_rules" MUST include: 'POV characters never use real-world publishing/genre vocabulary ("isekai," "protagonist," "trope," "genre," "plot armor," "chapter," "narrator") to describe their own situation, unless the work is explicitly metafictional. Characters should think and speak as people in their world, not as writers or readers.'
- All section headers and focus areas must align directly with what the prompt templates require.

=== FINAL CHECK (verify before responding — configs missing these are REJECTED) ===
Each generation prompt field MUST contain its literal placeholders, character-for-character:
- "gen_world_prompt": {{seed}} AND {{voice_part2}}
- "gen_characters_prompt": {{seed}} AND {{world}} AND {{voice_part2}}
- "gen_outline_prompt": {{seed}} AND {{world}} AND {{characters}} AND {{voice_part2}} AND {{premise_arc_beats}}
- "gen_outline_part2_prompt": {{part1}}
- "gen_canon_prompt": {{seed}} AND {{world}} AND {{characters}}
- "gen_chapter_title_rewriter_prompt": {{outline}} AND {{seed}}
Re-read your own JSON output and confirm every placeholder above is present.
"""


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
            timeout=300
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
            timeout=300
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(config1, indent=2))
    print(f"✅ Genre config written to {out_path}", file=sys.stderr)
    print(f"   Genre: {config1['genre_name']}")
    print(f"   Chapters: {chapter_count}, ~{estimated_words:,} words")
    print(f"   Foundation dims: {[d['key'] for d in config1['evaluation']['foundation']['dimensions']]}")
    print(f"   Chapter dims: {[d['key'] for d in config1['evaluation']['chapter']['dimensions']]}")


if __name__ == "__main__":
    main()
