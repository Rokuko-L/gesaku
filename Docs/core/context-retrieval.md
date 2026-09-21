# Context Retrieval (`core/retrieval.py`)

Host-scoped context packs for draft and revision prompts. Python decides
what the writer may see; generation stays one-shot. Sealed canon never
enters a writer pack (`canon.writer_view_md` remains the only writer-facing
canon owner).

## Mode

| `GESAKU_RETRIEVAL_MODE` | Behavior |
|---|---|
| `dump` (default) | Full `characters.md` + `world.md` — pre-upgrade behavior |
| `scoped` | Beat-scoped sections only; fallback to full text if a bible has zero hits |

Unknown values resolve to `dump`. Owner of the knob: this module
(`retrieval.retrieval_mode()`), not call sites.

## Pack shape

```text
RetrievalPack
├── mode
├── characters_block / world_block   # prompt-facing text
├── canon_view                      # passed through, not re-derived here
├── query_terms                     # deterministic outline/brief proper nouns
├── character_hits / world_hits     # section headings selected
├── character_fallback / world_fallback
└── to_telemetry() → dict
```

## How scoped selection works

1. Query terms from chapter outline, orientation facts, optional brief/old draft heads (no LLM). Schema labels (`Role`, `Age`, …) are stopwords.
2. Character sections (`##` / `###`) score on heading-name and body hits; narrator-ish first section gets a small boost. **Parent sections own their child subsections** (markdown section semantics: end at next heading of same-or-higher level).
3. World sections: selectable candidates are `##` (level ≤ 2); those parents already carry `###` children in the block span.
4. Named budgets: `MAX_CHARACTER_SECTIONS=6`, `MAX_WORLD_SECTIONS=4`.
5. Zero hits on a non-empty bible → that block falls back to the full text and sets `*_fallback=True`.
6. Honest telemetry: if a scoped "hit" is ≥ `FULLTEXT_HIT_RATIO` (0.8) of the source file, it is marked fallback — that is dump, not scope.

## Deferred vs plan table

| Plan item | Status |
|---|---|
| `not_established` pack field | Deferred — sealed denylist stays at draft call site via `writer_view_md` / hygiene; pack does not own sealed text |
| `open_debts` passthrough on pack | Deferred — still assembled in `draft_chapter` guardrails, not retrieval |
| Premise-beat query terms (ch1) | Deferred — outline text already contains premise beats; explicit injection not required for scoped mode |

## Call sites

| Module | Use |
|---|---|
| `pipeline/draft_chapter.py` | draft prompt world/character blocks |
| `pipeline/gen_revision.py` | revision prompt blocks (outline + brief + old draft head) |

Telemetry: `projects/<name>/eval_logs/retrieval_chNN.json` (atomic, fail-soft with stderr WARN).
`llm_events.jsonl` still records `prompt_chars` for size deltas.

## Invariants

- `core/retrieval.py` must not import `pipeline/`.
- Mode default is `dump` until Phase 6 A/B validates `scoped`.
- Under-retrieval must never produce empty world/character blocks when source files exist.
- Sealed canon is never read by this module; writer-facing canon comes only from `canon.writer_view_md` at the call site.

