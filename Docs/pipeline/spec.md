# GESAKU: Reproducible Novel Pipeline

## Overview

This document captures the full automated pipeline for generating,
drafting, and revising a novel from a seed concept. Derived from the
production of "The Second Son of the House of Bells" (75k words, 23
chapters, 5 revision cycles).

The goal: a user provides a seed concept. Everything else is automated.

---

## Master Branch (Framework)

Master contains no story-specific content. It is the reusable base.

```
FRAMEWORK (reusable, never edited by the pipeline):
  README.md            -- project overview
  WORKFLOW.md          -- step-by-step human guide
  PIPELINE.md          -- this file (automation spec)
  program.md           -- agent instructions per phase
  CRAFT.md             -- craft education (plot, character, world, prose)
  ANTI-SLOP.md         -- word-level AI tell detection
  ANTI-PATTERNS.md     -- structural AI pattern detection

TEMPLATES (empty shells, filled per-novel on branch):
  voice.md             -- Part 1 (guardrails) permanent; Part 2 blank
  world.md             -- section headers only
  characters.md        -- structure template only
  outline.md           -- structure template only
  canon.md             -- empty with instructions
  MYSTERY.md           -- blank template
  state.json           -- {phase: "foundation", iteration: 0, debts: []}

TOOLS (the pipeline machinery):
  Stage scripts live under `foundation/` or `pipeline/`; `uv_run` executes
  them from the repo root, so a call site must name the directory
  (`"pipeline/apply_cuts.py"`). Naming one without its directory makes the
  subprocess fail to start — and if the failure is swallowed, the stage
  silently becomes a no-op that still reports success.
  Foundation:
    seed.py              -- generate 10 seed concepts
    gen_world.py         -- seed → world.md
    gen_characters.py    -- seed + world → characters.md
    gen_outline.py       -- seed + world + chars → outline.md (part 1)
    gen_outline_part2.py -- outline + chars → foreshadowing ledger
    gen_canon.py         -- world + chars → canon.md (hard facts)
    voice_fingerprint.py -- trial passages → voice.md Part 2

  Drafting:
    draft_chapter.py     -- write a single chapter with anti-pattern rules
    run_drafts.py        -- batch sequential chapter drafter

  Evaluation:
    evaluate.py          -- mechanical slop scorer + LLM judge
                            modes: --phase=foundation, --chapter=N, --full

  Revision:
    adversarial_edit.py  -- "cut 500 words" judge → classified cut list
    compare_chapters.py  -- head-to-head Elo tournament
    reader_panel.py      -- 4-persona novel-level evaluation
    gen_revision.py      -- rewrite chapter from a revision brief
    build_arc_summary.py -- regenerate arc_summary.md from chapters
    build_outline.py     -- regenerate outline.md from chapters

  Export:
    typeset/novel.tex    -- LaTeX template (EB Garamond, trade paperback)
    typeset/build_tex.py -- chapters/*.md → chapters_content.tex
    typeset/build_epub.py -- chapters/*.md → novel.epub (stdlib zipfile, no toolchain)

  Orchestrator:
    run_pipeline.py      -- NEW: fully automated pipeline runner

CONFIG:
  .env.example           -- API key template
  pyproject.toml         -- Python dependencies (httpx, dotenv)
  .python-version
  .gitignore
```

---

## Per-Novel Branch (Generated)

Everything below is created automatically on a branch.

```
  seed.txt               -- chosen seed concept
  world.md               -- filled world bible
  characters.md          -- filled character registry
  outline.md             -- filled chapter outline + foreshadowing ledger
  voice.md Part 2        -- discovered voice identity
  canon.md               -- accumulated hard facts
  MYSTERY.md             -- central mystery (author-only)
  chapters/ch_*.md       -- the prose
  state.json             -- current phase, scores, debts
  results.tsv            -- experiment log (every keep/discard)
  arc_summary.md         -- chapter summaries for panel evaluation
  edit_logs/*.json       -- adversarial cuts, panel results, tournament
  eval_logs/*.json       -- full evaluation results
  briefs/*.md            -- revision briefs (input to gen_revision.py)
  typeset/novel.pdf      -- typeset PDF
  typeset/novel.epub     -- e-book (built unconditionally; --no-epub opts out)
```

---

## THE PIPELINE

### Phase 0: Setup

```
INPUT:  seed.txt (user-provided or generated via seed.py)
OUTPUT: branch created, .env configured

1. git checkout -b gesaku/<tag>
2. Verify .env has ANTHROPIC_API_KEY
3. Verify seed.txt exists and is specific enough
   (world-differentiator, central tension, cost/constraint, sensory hook)

Preflight (sanity_check, runs before any LLM call):
  - .env present; provider key present (custom gateways may be keyless → WARN)
  - seed.txt or --notes present
  - genre available (--genre / GESAKU_GENRE / active_genre.json)
  - PROBE the resolved endpoint with a 1-token request:
      unreachable  → FAIL, refuse to launch
      HTTP 404     → FAIL, model name does not resolve
    A doomed launch used to burn 10+ minutes before the first real error.
```

### Phase 1: Foundation

```
INPUT:  seed.txt
OUTPUT: world.md, characters.md, outline.md, voice.md, canon.md, MYSTERY.md
EXIT:   foundation_score > 7.5 AND lore_score > 7.0

Loop:
  1. gen_world.py        → world.md (lore, magic system, geography, factions)
  2. gen_characters.py   → characters.md (wound/want/need/lie, speech, sliders)
  3. gen_canon.py        → canon.md (hard facts tagged visible_from=N;
                           sealed facts stay out of drafting until chapter N)
  4. gen_outline.py      → outline.md part 1 (beats; plant-hygiene gated)
  5. gen_outline_part2.py → outline.md part 2 (foreshadowing ledger)
  6. Voice discovery: write 5 trial passages in different registers,
     select best, fill voice.md Part 2 with exemplars + anti-exemplars
  7. Define MYSTERY.md (the central secret the reader discovers)
  8. plant_hygiene.json  → pre-reveal leak + action-plant coverage report
  9. evaluate.py --phase=foundation
  10. If score improved → git commit. If worse → git reset --hard HEAD~1.
  11. Identify weakest dimension → target next iteration at it.

Per-artifact checkpointing:
  Each step is skipped when its output already exists and passes a cheap
  validity check (`_foundation_artifact_ok`: non-trivial size, and all
  chapter headers present for the outline). Part 2 is gated on an explicit
  **marker file** (`.outline_part2.done`, `paths.get_outline_part2_path()`):
  written last by `gen_outline_part2`, deleted by `gen_outline` whenever it
  rewrites `outline.md`. No prose heuristic can distinguish a part-1 outline
  from a part-2-polished one, and the old "the word FORESHADOW appears
  somewhere" test marked every iteration dirty, re-running the whole refine
  pass each time. A crash mid-foundation therefore resumes at the missing
  artifact instead of regenerating world, characters, canon, and outline —
  that cost 30-60 min of LLM calls per crash. `--from-scratch` wipes the
  files (and every sidecar: the micro-plant store, plant hygiene, premise
  validation, outline intermediates, retry feedback), which is the explicit
  invalidation path. Deeper gates (premise beats, plant hygiene) still run
  every iteration on top of the checkpoint.

  The loop's own progress must survive that checkpoint logic:
  `run_foundation` re-asserts `state["iteration"] = i` after its
  `state.update(load_state())` sync, or the save writes the stale on-disk
  iteration and a crash restarts the iteration count at 1.

Sealed foundation + twist stories:
  - Facts tagged `visible_from=N` (N>1) are withheld from writer and judge
    prompts for chapters < N (`core/canon.py` writer_view/judge_view).
  - `gen_canon.normalize_foundation_bullets` defaults a *plain* bullet to
    `visible_from=1`, but passes a bullet that merely **attempts** a tag
    (`core/canon.TAG_ATTEMPT_RE`) through untouched. Rewriting those to
    `visible_from=1` here would publish a sealed fact before `parse_canon`
    ever sees it and make the fail-closed (`MALFORMED_SEAL`) path
    unreachable; the generator instead retries with the bad line reported.
  - Pre-reveal outline beats must be action-shaped; meaning-laden language
    and sealed lexicon fail `core/plant_hygiene.py` (retry in gen_outline).
  - Characters named in sealed facts must appear as agents in pre-reveal
    chapters (coverage floor) so a post-reveal retrofit has material.
  - When the reveal chapter is first kept, `pipeline/retrofit_reveal.py`
    rewrites ch 1..R-1 to thicken existing plants only (no new plot).
    Under-planted outlines block the pass (exit 2); continuity scan in
    `retrofit_report.json` is informational and never gates keep/discard.

Key learnings:
  - Foundation typically takes 5-15 iterations
  - The evaluator weights lore interconnection at 40% — magic must
    affect politics, history must explain factions, geography must
    shape culture
  - Cross-layer consistency check on EVERY iteration
  - Canon should have 400+ entries before exiting foundation
  - Voice discovery is a separate sub-loop: write trial passages,
    evaluate, select, refine

Known behaviors (smoke run, 4-chapter test):
  - Genre-framework Pass 2 requires generated prompt templates to contain
    literal placeholders ({voice_part2}, {seed}, ...). The meta-prompt ends
    with a FINAL CHECK checklist, and missing placeholders are auto-repaired
    deterministically instead of failing the run.
  - Foundation scores can plateau below the exit threshold when the
    LLM-generated rubric is calibrated harshly (the judge prompt tells it to
    revise down scores above 7). FIXED: after 3 consecutive non-improving
    iterations the loop proceeds with the best docs (FOUNDATION_PLATEAU_ITERS);
    the gate itself is overridable via GESAKU_FOUNDATION_THRESHOLD.
    Improvement is strict (pipeline_infra.foundation_plateau): a TIED score
    counts as a stall, so a judge returning flat scores still exits early.
  - --notes accepts inline text or a file path; path resolution only kicks
    in for plausible paths (<260 chars, single line).
  - Writer chronically undershoots Chapter 1 word budgets. MITIGATED:
    draft prompts now carry explicit per-beat word budgets, and undersized
    retries get concrete expansion numbers (actual vs target vs minimum).
```

### Phase 2: First Draft

```
INPUT:  all foundation docs
OUTPUT: chapters/ch_01.md through ch_NN.md
EXIT:   all chapters drafted with score > 6.0

For each chapter in outline order:
  1. Load context window:
     - voice.md (full)
     - world.md (full)
     - characters.md (full)
     - This chapter's outline entry
     - Previous chapter's last ~600 words
     - Next chapter's outline (for continuity)
     - Canon view as of this chapter (public foundation + core +
       prior As-of only — sealed visible_from>N facts withheld)
  2. draft_chapter.py → chapters/ch_NN.md
  3. evaluate.py --chapter=NN  (judge sees the same chapter-scoped canon view)
  4. If score > 6.0 → keep, commit. If < 6.0 → discard, retry (max 5).
  5. Extract new canon entries from eval output → append to canon.md
  6. If this chapter is the sealed reveal chapter → run retrofit_reveal.py
     once (state flag reveal_retrofit_done)
  7. Log to results.tsv

Quality gates are not fatal:
  - A judge that fails 3× (timeout, unparseable output) does NOT kill the
    run. The attempt is logged as `unevaluated` and drafting continues; the
    draft itself was fine, only the critic was broken.
  - A failed repair_slop re-eval falls back to the pre-repair score instead
    of raising.
  - A failed Opus review round warns and falls through to export. The novel
    is already written — a broken critic pass must not lose the work.
  Structural failures (missing file, invalid JSON, zero chapters) still
  abort; drift/hygiene/low-score are warnings.

Post-draft cleanup:
  7. Mechanical slop pass (evaluate.py regex scanner) across all chapters
  8. Fix recurring AI patterns identified in early chapters
     (these compound — fix them before revision phase)
  9. Update state.json phase to "revision"

Key learnings:
  - Forward progress over perfection. 6.0 is good enough.
  - Chapters 1-6 score higher than 7-24 (freshness decay).
    After ch 6, add anti-pattern rules to the writer prompt.
  - Batch the second half (ch 11+) — it's faster and the quality
    is consistent enough.
  - The mechanical slop pass catches ~200 instances of tier-1 banned
    words, em-dash overuse, and sentence-length uniformity.
  - Total drafting time: ~8-16 hours of API calls for 25 chapters.
```

### Phase 3: Revision

This is where the real quality happens. 3-6 cycles, each with a
specific focus. Stop when scores plateau across 2 consecutive cycles.

```
CYCLE 1: BASELINE & DIAGNOSIS

  The in-loop steps are `pipeline/phases/revision.py`; the stage scripts it
  runs live in `pipeline/`. Steps 1 and 2 are each gated on the script
  existing at its real path — a gate that checks the wrong directory silently
  disables the step while still paying for the two full-novel evals that
  measure it.

  1. adversarial_edit.py all
     → edit_logs/chNN_cuts.json for all chapters
     → Systemic pattern identified (expect OVER-EXPLAIN at ~30-35%)
  2. compare_chapters.py
     → edit_logs/tournament_results.json (Elo rankings)
  3. Apply top cuts:
     Focus on OVER-EXPLAIN + REDUNDANT (together ~55-60% of all cuts)
     Target: chapters with >17% fat
     Method: automated quote-matching removal
     Expect to cut ~2000-3000 words (~3-4% of novel)
  4. reader_panel.py
     → edit_logs/reader_panel.json
     4 personas: editor, genre reader, writer, first reader
     Each answers: momentum_loss, earned_ending, cut_candidate,
       missing_scene, thinnest_character, best_scene, worst_scene,
       would_recommend, haunts_you, next_book
  5. Identify CONSENSUS items (3/4 or 4/4 agreement):
     These are the revision priorities.
  6. Git commit: "Cycle 1: adversarial + panel baseline"
```

```
CYCLE 2-3: STRUCTURAL REVISIONS (address panel consensus)

  For each consensus item, in priority order:
    a. CUT CANDIDATE (4/4 agreement):
       Write compression brief → gen_revision.py
       Target: cut 40-60% of chapter words
       Keep: the 2-3 essential beats the panel identified
       WARNING: don't over-compress. 1700w is too thin for any chapter.
       Sweet spot: 2200-3000w for a compressed chapter.

    b. MISSING SCENE (4/4 agreement):
       Write expansion brief → gen_revision.py for the target chapter
       OR: surgical patch if the scene is <400 words
       Key: the brief must specify what to KEEP (existing good material)
       and what to ADD (the missing beat)

    c. THIN CHARACTER (4/4 agreement):
       Identify 1-2 existing scenes where the character appears
       Add a private/unguarded moment the POV character catches
       Connect to the character's backstory in characters.md
       DON'T add a new scene — deepen an existing one

    d. WEAK SCENE (3/4 agreement):
       Write dramatization brief → gen_revision.py
       Change HOW information arrives, not WHAT information
       Convert "reading a document" → investigation/confrontation
       Convert "briefing" → confrontation with resistance

    e. CONSISTENCY / TIMELINE:
       Search for contradictions (years, ages, sequence of events)
       Fix in canon.md + all source files + chapter references
       The 10yr/12yr distinction will happen. Plan for it.

    f. CHAPTER RENUMBERING:
       If chapters were merged/deleted, ALL internal titles need updating
       Use a script, not manual edits

  After each structural change:
    evaluate.py --chapter=N for affected chapters
    Keep if improved, discard if not
    Git commit with detailed message

  After kept rewrites in a cycle:
    resync_canon_after_cycle() rebuilds a replaceable ## Revision Sync
    block in canon.md from post-revision eval new_canon_entries.
    Draft-era ## As of Chapter N sections stay; the sync block does not
    stack across cycles.

  evaluate.py --full → get novel-level scores
  Git commit: "Cycle N: structural revisions from panel"

  Early stop — two independent signals:
    - PLATEAU: |score change| < GESAKU_PLATEAU_DELTA for a cycle after
      MIN_REVISION_CYCLES. Catches a judge that has run out of signal.
    - DECLINE: the score dropped for GESAKU_DECLINE_STREAK (default 2)
      consecutive cycles. The plateau test only fires on a *stable* score,
      so a monotonically falling sequence would otherwise run to
      MAX_REVISION_CYCLES burning hours of LLM time. Export ships the best
      checkpoint either way, so stopping early costs no quality.
```

```
CYCLE 4-5: TARGETED IMPROVEMENTS (address eval callouts)

  evaluate.py --full produces:
    - weakest_dimension (usually pacing_curve)
    - weakest_chapter
    - top_suggestion (specific fix)
    - per-dimension scores and commentary

  Common patterns and fixes:
    a. PACING (always the stubborn score):
       - Act II investigation rhythm repetitive →
         compress the weakest investigation chapter, vary scene types
       - Act III compressed → expand ally-gathering and climax
       - Reveals fire too fast → add breathing beats between reveals
       WARNING: fixing one stretch exposes the next. Pacing=7 may be
       a structural ceiling for LLM-evaluated novels.

    b. CHAPTER TOO SHORT for structural importance:
       Write expansion brief → gen_revision.py
       Target: +800-1500 words
       Focus: physical accumulation, dread, silence-with-duration
       The brief should specify WHAT BEATS to expand, not just "make longer"

    c. REPEATED PHRASES across chapters:
       Search for the phrase across all chapters
       Change all but the most impactful instance
       Common AI repeats: opening descriptions, emotional formulas,
       "the way [X] did [Y]", triadic lists

    d. UNRESOLVED THREADS:
       Check foreshadowing ledger in outline.md
       Add resolution beats where threads were planted but never harvested
       Surgical patches, not full rewrites

  After fixes:
    evaluate.py --full → check scores improved
    If weakest_chapter changed → previous fix worked
    If scores unchanged after 2 cycles → stop, diminishing returns
```

```
CYCLE 6: POLISH (final pass)

  1. adversarial_edit.py all → fresh cut data on rewritten chapters
  2. Apply cuts from chapters that were rewritten in cycles 2-5
  3. Slop pass: evaluate.py per-chapter on rewritten chapters
  4. reader_panel.py → final validation
  5. Rebuild founding docs
```

```
PHASE 3b: OPUS REVIEW LOOP (deep, prose-level refinement)

  After the automated cycles, switch to Opus for the final quality push.
  This is the evaluation that actually catches prose problems, structural
  repetition, character thinness, and ethical gaps.

  Tool: review.py
  Model: Claude Opus (the best available for literary analysis)
  Prompt: "Read the below novel. Review it first as a literary critic
    (like a newspaper book review) and then as a professor of fiction.
    In the later review, give specific, actionable suggestions for any
    defects you find. Be fair but honest. You don't *have* to find defects."

  Loop (max 4 rounds):
    1. review.py --output reviews.md
       Sends full manuscript to Opus. Gets dual-persona review.
    2. review.py --parse
       Extracts actionable items, severity, type.
       Classifies items: major/moderate/minor, qualified/unqualified.
    3. STOPPING CONDITION:
       Stop if: no major unqualified items remain
       Stop if: >50% of items are qualified/hedged
       Stop if: ≤2 items found
       These signals mean the reviewer is running out of real problems.
    4. Address top items:
       - gen_brief.py --auto → picks weakest chapter, generates brief
       - gen_revision.py → rewrites chapter from brief
       - Mechanical fixes (apply_cuts.py) for pattern issues
       - Surgical patches for targeted additions
    5. Commit, repeat.

  Key learnings from the Bells production (6 review rounds):
    - The same issues surface repeatedly until fixed (middle pacing,
      tics, character depth). This is the signal to act.
    - When the language shifts from "the novel has problems" to
      "these are the costs of ambition" → stop revising.
    - The reviewer will ALWAYS find something. The stopping condition
      is about severity and qualification, not zero defects.
    - Items that persist across 3+ rounds may be structural to the
      novel's voice/approach, not bugs. Learn to accept them.
    - The reviewer's item severity is the guide:
      multiple major items → structural work needed
      few major, some moderate → targeted revisions, 2-3 more rounds
      all moderate/minor → polish only, 1-2 more rounds
      mostly qualified hedges → done, ship it
```

### Phase 4: Export

```
  0. Ship the peak, not the latest:
     If state.best_novel_score > state.novel_score, git-checkout the
     chapters from best_novel_commit and export that. Revision cycles keep
     edits that pass tolerance (a 0.8 regression on an LLM rewrite), so the
     last cycle is not necessarily the best one — one production run
     exported 6.86 when 7.65 already existed.
     The restore is exact, not additive (`_restore_best_novel`): chapters
     present now but absent from the peak commit are deleted (plain
     `git checkout <c> -- chapters` leaves them), and `open_callbacks.json`
     is restored alongside so the plant store still describes the prose on
     disk — or dropped when the peak predates the store.
  1. Normalize chapter titles (all # level, consistent format)
  2. typeset/build_tex.py → chapters_content.tex
  3. Edit typeset/novel.tex:
     - Set title, author name
     - Choose epigraph (from novel text, NOT a spoiler)
     - Set end-page text
  4. tectonic novel.tex → novel.pdf
  5. typeset/build_epub.py → novel.epub
     EPUB 3, structured as mimetype (first, STORED) + container.xml +
     content.opf + nav.xhtml + toc.ncx + one XHTML per chapter. Pure stdlib,
     so it needs no toolchain and is attempted unconditionally; a failure
     warns and continues (a book without an e-book edition is still a book).
     Skip with `--no-epub`. The identifier is a UUIDv5 of project+title, so
     re-exporting does not mint a new book identity.
  6. Git commit: "export: manuscript, outline, arc summary, PDF[, EPUB]"
```

---

## KEY LEARNINGS (from the Bells production)

### What the evaluator rewards
  - Theme coherence hits ceiling (10) early if the seed has a strong
    central question. Build the magic system AS the theme.
  - Voice consistency (9) holds if you never break POV and keep the
    craft vocabulary native.
  - Foreshadowing (9) requires a ledger maintained from foundation
    through drafting. Every plant needs a payoff.

### What the evaluator penalizes
  - Pacing (7) is structurally stubborn. Investigation chapters
    (go-learn-blocked) repeat a rhythm that the evaluator catches.
    Fix one stretch, it finds the next. Accept 7 as the likely ceiling
    unless you restructure the plot.
  - OVER-EXPLAIN is the #1 AI writing pattern (~32% of adversarial cuts).
    The narrator explains what scenes already showed. Cut aggressively.
  - REDUNDANT is #2 (~26%). Same insight restated 3-4 times. Once is enough.

### What the reader panel catches that the evaluator doesn't
  - "Checklist of yeses" — when allies all agree without friction
  - Missing emotional scenes between key characters
  - Characters who are "more mechanism than person"
  - Scenes that need to be messier, more human, less choreographed
  - The difference between a scene that "works" and one that "lives"

### Dangerous patterns
  - Over-compressing: cutting a chapter below 1800w makes it the new
    weakest. Sweet spot is 2200-3000w for compressed chapters.
  - Expansion bloat: gen_revision.py adds ~30% more words than briefed.
    A brief targeting 3200w will produce 3800-4200w.
  - Score chasing: after cycle 4, fixing one score often drops another.
    Arc went 9→8→9 when we over-compressed Ch 11.
  - The evaluator rotates "weakest chapter" — chasing it is whack-a-mole.
    After 2 rotations, stop.

### Timeline estimates
  Phase 1 (Foundation):    2-4 hours API time, 5-15 iterations
  Phase 2 (First Draft):   8-16 hours API time, 23-30 chapters
  Phase 3 (Revision):      4-8 hours API time, 3-6 cycles
  Phase 4 (Export):         30 minutes
  TOTAL:                    ~15-30 hours API time for a 75k word novel

---

## WHAT NEEDS BUILDING FOR FULL AUTOMATION

### Already exists (on branch, needs merge to master):
  - gen_revision.py
  - reader_panel.py
  - build_arc_summary.py
  - build_outline.py
  - voice_fingerprint.py
  - typeset/novel.tex + build_tex.py

### Needs building:
  1. run_pipeline.py — Orchestrator that runs all phases
     - Phase 1: loop foundation generation + evaluation
     - Phase 2: sequential drafting with retry logic
     - Phase 3: revision cycles with automated brief generation
     - Phase 4: export
     - Score plateau detection (stop when Δ < 0.5 across 2 cycles)
     - Automated brief writing from panel feedback + eval callouts

  2. gen_brief.py — Auto-generate revision briefs from structured feedback
     Input: panel JSON + eval JSON + chapter text
     Output: a revision brief (.md) suitable for gen_revision.py
     This is the key automation gap — currently briefs are hand-written.

  3. apply_cuts.py — Batch cut applicator
     Input: edit_logs/chNN_cuts.json
     Output: patched chapter files
     Filters by cut type (OVER-EXPLAIN, REDUNDANT)
     Handles quote-matching failures gracefully

  4. Clean master branch:
     - Merge tools from branch (gen_revision, reader_panel, etc.)
     - Strip story-specific content from template files
     - Add .env.example
     - Update WORKFLOW.md to reference PIPELINE.md
     - Update README.md with the full automation story

---

## THE ORCHESTRATOR (run_pipeline.py spec)

```python
# Pseudocode for the fully automated pipeline

def run_pipeline(seed_path, tag="run1"):
    setup(tag, seed_path)
    
    # Phase 1
    while state.foundation_score < 7.5 or state.lore_score < 7.0:
        weakest = evaluate_foundation()
        improve_layer(weakest)
        score = evaluate_foundation()
        if score > state.foundation_score:
            commit(f"foundation: improve {weakest}")
            state.foundation_score = score
        else:
            reset()
    
    state.phase = "drafting"
    
    # Phase 2
    for ch in range(1, state.chapters_total + 1):
        for attempt in range(5):
            draft_chapter(ch)
            score = evaluate_chapter(ch)
            if score > 6.0:
                commit(f"drafting: ch {ch} score {score}")
                break
            else:
                reset()
        mechanical_slop_pass(ch)
    
    state.phase = "revision"
    
    # Phase 3
    prev_score = 0
    for cycle in range(1, 7):
        # Diagnosis
        cuts = adversarial_edit_all()
        apply_top_cuts(cuts, types=["OVER-EXPLAIN", "REDUNDANT"])
        panel = run_reader_panel()
        
        # Structural fixes
        for item in panel.consensus_items():
            brief = generate_brief(item, panel, cuts)
            revise_chapter(item.chapter, brief)
            if evaluate_chapter(item.chapter) > previous:
                commit(f"cycle {cycle}: {item.type}")
            else:
                reset()
        
        # Full evaluation
        score = evaluate_full()
        if abs(score - prev_score) < 0.5 and cycle >= 3:
            break  # plateau
        prev_score = score
        
        # Targeted fixes from eval
        fix_eval_callouts(score.top_suggestion)
        slop_pass(rewritten_chapters)
        
        commit(f"Cycle {cycle} complete: score {score}")
    
    # Phase 4
    rebuild_docs()
    typeset()
    export()
```

---

## Prose-emergent micro-plants (open callbacks)

Separate from outline-tag debts (`state["debts"]` / `[Plant: slug]`).

- After every **kept** chapter (the drafting keep paths, targeted revision
  keeps, and Opus-review keeps), `pipeline.phases.common.on_chapter_kept` runs
  `pipeline/extract_micro_plants.py <ch>` (fail-soft; never blocks the keep).
- **The store is chapter-scoped state, and it is coupled to the prose.** Two
  rules follow, and both are load-bearing:
  1. On a **revert** the chapter is restored to its *best-scoring* commit,
     which can be older than the version the store was last extracted from —
     so the revert path must also re-extract (`--reextract` drops this
     chapter's plants, then rebuilds from what is now on disk).
  2. **Staging is a keep-path-only promise.** On a keep, extraction runs
     before the commit and the chapter + `open_callbacks.json` are staged
     together (`common.stage_chapter_with_callbacks`), because
     `git_commit_staged` commits the whole index. On a revert there is no
     commit, so the store must **not** be staged: an uncommitted staged store
     would be discarded by a later `git reset --hard` (restoring a stale
     copy) or swept into the next chapter's keep commit under the wrong
     message. Reverts re-extract into the working tree only; the next
     cycle-end `git_add_commit` (`add -A`) picks the store up.
  `git_reset_hard` also excludes `open_callbacks.json` from its `git clean`,
  since an untracked store would otherwise be deleted outright on a reset.
  That exclusion only helps while the file is untracked (first run); once
  tracked, `git clean` skips it anyway and the keep-path staging rule is
  what keeps it consistent.
- Drop + rebuild is atomic from the store's point of view: `drop_source_chapter`
  only persists together with a successful re-extraction
  (`extract_micro_plants.py` saves once, at the end), so a failed extract
  leaves the previous store intact.
- Extract asks the judge for **at most 1** concrete callback candidate
  (object / phrase / promise / injury) and which open callback ids were paid off
  with changed meaning.
- Store: `projects/<name>/open_callbacks.json` via `core/micro_plants.py`
  (atomic writes; max 8 open; expire after 12 chapters; near-dup filter).
- **Drafting does not inject callbacks.** Soft optional list appears only in
  `pipeline/gen_revision.py` (`soft_inject_block`) with
  “prefer nothing over a forced reference.”
- Targeted revision keeps re-extract with `--reextract` (drops plants whose
  `source_chapter` matches the revised chapter).

Export `pipeline/build_outline.py` clusters free-text plants/harvests by token
Jaccard + union-find (near-duplicates collapse; plant only links to a later or
same-chapter harvest). Statuses: `paid off` (plant+harvest), `open` (plant
only), `recalled` (harvest only). The webui ledger surfaces planned major
threads, the clustered emergent ledger, and open callbacks.

Offline tests: `tests/test_micro_plants.py`.

---

*This pipeline was derived from 60+ commits, 5 revision cycles,
2 reader panels, 2 adversarial edits, and ~20 hours of agent time
producing a 75,000-word fantasy novel.*
