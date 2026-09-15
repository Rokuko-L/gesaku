=== USER INPUT ===
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
