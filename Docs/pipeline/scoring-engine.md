# Scoring Engine (`pipeline/evaluate.py`)

Every keep/discard decision in the pipeline comes from this module. Two
independent signals are combined: a **mechanical slop score** (no LLM) and
an **LLM judge score**.

```
final_score = max(0, judge_overall_score - slop_penalty - length_penalty
                      - orientation_penalty)
```

Module layout — the scoring engine is split by concern:

| Module | Role |
|---|---|
| `pipeline/slop.py` | Mechanical detectors + penalty model (no LLM) |
| `pipeline/orientation.py` | Orientation-fact coverage check (no LLM) |
| `pipeline/eval_prompts.py` | Judge prompt construction from the genre config |
| `pipeline/evaluate.py` | Loads context, calls the judge, applies penalties |

## Mechanical Slop Score (`slop_score(text)`)

Deterministic detectors, each with its own penalty cap (global cap 4.0):

| Detector | Catches |
|---|---|
| Tier 1/2/3 word lists | "delve", "utilize", filler phrases |
| `FICTION_AI_TELLS` | prose clichés ("eyes widened", "a wave of X") |
| `STRUCTURAL_AI_TICS` | rhetorical formulas ("not just X, but Y") |
| `PROSE_TIC_PATTERNS` | density-based tic families — penalized only *per 3k words above threshold* (a single "not X, but Y" is human; a cluster is a machine) |
| Staccato runs | 3+ consecutive sentences ≤4 words |
| Em-dash density, sentence-length CV, transition-opener ratio | rhythm uniformity |
| Non-Latin script | glyphs EBGaramond can't render |

## LLM Judge

- `call_judge_json(prompt, model=...)` — judge call with self-correction
  retries for syntax AND schema failures (see
  [../core/output-validation.md](../core/output-validation.md)).
- Chapter eval prompt includes voice, world summary, characters, a
  **chapter-scoped canon view** (`core.canon.judge_view_md`), the chapter's
  outline entry, and the previous-chapter tail.
- The judge does **not** receive sealed foundation facts (`visible_from > N`)
  or future `## As of Chapter` sections. Sealed truths are withheld so early
  chapters are not scored against knowledge the reader does not have.
- The judge also emits `new_canon_entries` (core vs incremental) and
  `unexplained_references`, which feed later chapters' canon files.

## Roadmap (not in v1)

- **Author-continuity judge**: a separate, non-blocking check that sees
  sealed foundation + full outline + the chapter and reports `mask_breaks`
  (e.g. A doing something logistically impossible if the mask were true) and
  `plant_gaps`. Must never affect `chapter_gate` / keep-discard.
  `pipeline/retrofit_reveal.py` already writes an informational
  continuity scan to `retrofit_report.json`; a dedicated LLM judge is still
  outstanding.

Related: [../pipeline/spec.md](spec.md) for sealed foundation + retrofit.

## Post-Judge Penalties (`evaluate_chapter`)

1. **Slop penalty** — subtracted from raw judge score (`raw_judge_score`
   preserved in eval logs).
2. **Length penalty** — ±3.0 scaled outside an 80%–125% window of target
   words (climax/finale chapters get a 155% ceiling).
3. **Orientation penalty** — up to −2.0 when ≥2 outline Orientation Facts
   aren't dramatized (synonym-aware matching).

## From Score To Verdict

The score alone doesn't decide keep/discard — a tolerance budget does, and
the budgets differ by edit type on purpose (see
[state-and-git.md](state-and-git.md#tolerances-keepdiscard-policy)):

- prose rewrite (adversarial/targeted/review): keep if `post >= baseline - 0.8`
- mechanical cuts: keep if `post >= baseline - 0.05`
- drafting: keep at gate; below it, a **near-clean** draft (within 1.0 of the
  gate with negligible mechanical penalties) is kept rather than regenerated,
  because deleting it and drafting blind regresses more than the miss costs

Note `baseline` is `max(pre_score, historical_best_of_chapter)`, not the
previous attempt — a chapter that scored well in an earlier cycle cannot be
allowed to drift down through repetitive "improvements".

Related: [state-and-git.md](state-and-git.md) ·
[../core/output-validation.md](../core/output-validation.md) · [spec.md](spec.md)

## Eval Logs

Each run writes `projects/<name>/eval_logs/<timestamp>_<mode>.json` with the
full result dict. `_latest_chapter_score(n)` reads these back for the
full-novel evaluation's chapter metadata.

Related: [state-and-git.md](state-and-git.md) ·
[../core/output-validation.md](../core/output-validation.md) · [spec.md](spec.md)
