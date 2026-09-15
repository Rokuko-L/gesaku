# gesaku — Overview

An autonomous pipeline that writes a complete novel from a single premise:
genre config → world/characters/outline/canon → chapter drafts → revision
loops → typeset PDF. Every phase scores its output and only keeps
improvements (modify → evaluate → keep/discard).

> **Start here if unsure which doc to read.** This page cross-links everything.

---

## Code Architecture

```
core/             Shared library — no pipeline-specific logic
├── paths.py        Project root/state resolution, folder+file path helpers
│                   (incl. per-artifact sidecars), prompt loader, atomic JSON
├── llm.py          Multi-provider client (call_llm: anthropic + openai
│                   dialects, any compat endpoint) + response extraction
├── json_repair.py  Healing JSON parser (parse_json_response), re-exported by
│                   llm.py for the documented entry point
├── canon.py        Canon.md parse + chapter-scoped writer/judge views;
│                   sealed foundation (visible_from) + denylist terms
├── plant_hygiene.py Outline plant hygiene: pre-reveal leak regex + action-plant
│                   coverage floor
├── micro_plants.py  Prose-emergent micro-plant store (open_callbacks.json) +
│                   plant↔harvest clustering for the rebuilt ledger
├── outline.py      Outline text ops: chapter headings, premise beats,
│                   plants/harvests validation, debt extraction
├── textstats.py    Context windows (tail/head), repetition detection
├── novel_tex.py    Default LaTeX novel.tex template generation
├── genre.py        Genre config loader + validator (active_genre.json);
│                   `chapters_total()` is the single owner of the chapter count
├── validation.py   Pydantic schema layer for LLM output (parse_validated)
└── mock_llm.py     Offline LLM mock for tests (MockLLM.install())

pipeline/         Orchestration and per-stage tooling
├── pipeline_infra.py Git plumbing, registry/state persistence, timeouts,
│                   tolerances, best-novel tracking, subprocess helpers
├── scores.py         Score parsing + chapter counting (raises on a missing key)
├── phases/           One module per pipeline phase:
│   ├── foundation.py   Phase 1 (per-artifact checkpoints)
│   ├── drafting.py     Phase 2 (judge failure != fatal)
│   ├── revision.py     Phase 3 cycles (adversarial, panel, targeted rewrites)
│   ├── review_loop.py  Phase 3b Opus review (non-blocking)
│   ├── export.py       Phase 4 (ships the peak, not the latest)
│   └── common.py       Shared canon-sync + post-keep hooks
├── preflight.py      sanity_check: fails fast on dead proxy / unknown model
├── slop.py           Mechanical slop detection (no LLM)
├── orientation.py    Outline orientation-fact coverage check
├── eval_prompts.py   Judge prompt construction (genre-config driven)
├── evaluate.py       Scoring engine: slop + judge + penalties (judge_view)
├── briefs/           Revision-brief generators, one module per feedback source
├── retrofit_reveal.py Post-reveal rewrite of ch 1..R-1 (coverage-gated)
└── ...               drafting/revision/export stage scripts

foundation/       Foundation-phase generators (one script per document)
├── gen_genre_framework.py / gen_world.py / gen_characters.py /
├── gen_outline.py / gen_outline_part2.py / gen_canon.py /
├── outline_gates.py    act ranges + tonal-drift judge
└── gen_title.py / seed.py

fuel/             Pipeline fuel — runtime LLM prompt material (see below)
prompts/          Static prompt templates (loaded via paths.load_prompt)
projects/<name>/  Per-novel isolated workspace (gitignored; own git repo)
tests/          Offline test suites
webui/            Operator console: server.py (FastAPI bridge, port 8600)
├── deps.py         project resolution + state helpers (shared by routes)
├── routes/         APIRouter modules: graph, settings, stream
└── frontend/       React 19 + Vite app; src/api/contract.js declares the
                    API shapes, client.js calls /api with fixture fallback

Root entry points: run_pipeline.py (orchestrator CLI — sequences phases, owns
the CLI), cli.py (`uv run gesaku` operator console), webui/server.py (FastAPI
bridge), install_fonts.py, _utf8.py (UTF-8 enforcement shim)
```

**Data flow:**

```
gen_*/stage scripts ──call──> llm.call_llm() ──> raw text
                    ──parse──> llm.parse_json_response()   (syntax heal)
                    ──validate──> validation.parse_validated()  (schema)
                    ──write──> paths.get_*_path() targets under projects/<name>/
run_pipeline.py orchestrates phases via pipeline_infra (state.json checkpoints,
git keep/discard per attempt, results.tsv score log)
```

---

## Documentation Map

| Doc | Contents |
|---|---|
| [workflow.md](workflow.md) | Step-by-step run guide |
| [pipeline/spec.md](pipeline/spec.md) | Full pipeline process spec (phases, revision loop) |
| [pipeline/state-and-git.md](pipeline/state-and-git.md) | state.json, registry, git keep/discard plumbing |
| [pipeline/scoring-engine.md](pipeline/scoring-engine.md) | How chapters get scored (slop + judge + penalties) |
| [core/path-resolution.md](core/path-resolution.md) | Project isolation, path helpers, atomic writes |
| [core/llm-client.md](core/llm-client.md) | API client, retries, truncation, JSON repair |
| [core/output-validation.md](core/output-validation.md) | Pydantic schemas for LLM output, self-correction retries |
| [core/prompt-management.md](core/prompt-management.md) | prompts/ directory and loader conventions |
| [systems/mock-testing.md](systems/mock-testing.md) | Testing pipeline code offline with MockLLM |
| [systems/console-bridge.md](systems/console-bridge.md) | webui FastAPI bridge: endpoints, run instructions, deferred scope |
| [reference/test-suites.md](reference/test-suites.md) | Offline suite index + how to run |
| [reference/project-refactor.md](reference/project-refactor.md) | Multi-project refactor record (completed) |
| [reference/archive/](reference/archive/) | Superseded docs (ANTI-PATTERNS.md, program.md, test-infra.md) — historical only |

## Pipeline Fuel — NOT documentation

`fuel/` holds **runtime LLM prompt material** consumed by the code.
Never treat these as agent docs, never "clean them up":

| File | Consumed by |
|---|---|
| `CRAFT.md` | `foundation/gen_world.py`, `foundation/gen_outline.py` |
| `ANTI-SLOP.md` | pattern source for the judge prompts |
| `voice.md` | template copied into each project by `run_pipeline.py` |
| `MYSTERY.md` | foundation-phase template (author's eyes only) |
| `notes_template.md` | user premise template |

## Key Rules (short version)

1. All file I/O through `paths.py` helpers — never hardcode project paths.
2. LLM JSON → Pydantic models in `validation.py`; feed
   `OutputValidationError.feedback` back into self-correction retries.
3. Static prompts live in `prompts/*.md`.
4. Tests must pass offline (`mock_llm.MockLLM`); suites in `tests/`.
5. Atomic JSON writes only (tmp + rename) — including sidecars and eval logs.
6. Import from the concern module directly — there is no umbrella module.
   Dependency direction is `core ← foundation/pipeline ← run_pipeline`;
   `foundation/` must not import `pipeline/`.
7. One owner per fact: the genre config owns the chapter count, `pipeline_infra`
   owns timeouts and tolerances. Mirror, never re-derive.
8. Config knobs live in one named table (`timeout_for`, `*_threshold`,
   `*_tolerance`) — no per-call-site magic numbers.
