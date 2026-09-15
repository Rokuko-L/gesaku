=== STRUCTURAL CONFIGURATION ===
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
