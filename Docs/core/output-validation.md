# Output Validation Layer (`core/validation.py`)

`llm.parse_json_response()` guarantees valid JSON but not valid *shape*.
Historically, a judge omitting `overall_score` or returning it as a string
silently poisoned downstream scores (`-1.0`, KeyError three phases later —
roughly half of all `fix:` commits in the git history). This module closes
that hole.

## Usage

```python
import validation

model = validation.parse_validated(
    validation.ScoreOutput, raw_text, context="Judge response")
data = model.model_dump()   # dict for legacy call sites
```

Raises `OutputValidationError` (subclass of `ValueError`) when the text has
no JSON or fails schema validation. `.feedback` carries an LLM-readable
explanation designed to be pasted into a self-correction retry prompt.

## Models

| Model | Required | Notes |
|---|---|---|
| `ScoreOutput` | `overall_score: float` (0–10) | Foundation/chapter evals. Extra keys allowed (dynamic genre dimensions). Coerces `"7.5"` and removes a literal `/10` suffix (`"6.1"` stays 6.1, `"10"` parses). |
| `NovelScoreOutput` | `novel_score: float` (0–10) | Full-novel eval. Same coercion. |
| `CompareOutput` | `winner: "A"\|"B"\|<chapter#>` | Head-to-head verdicts; normalizes case, accepts chapter numbers. |
| `TonalDriftVerdict` | `has_drift: bool` | Outline tonal-drift gate (`gen_outline.verify_tonal_drift`). `has_drift` is **required** — a judge that omits it must fail validation, not silently default to "no drift" (that is exactly how the gatekeeper went quietly dead once before). Accepts string booleans; coerces a lone `violations` string into a list. |
| `MicroPlantExtract` | none (both lists default) | Post-keep micro-plant extraction; caps `new_plants` at 4. |

## Where It's Wired

- `evaluate.call_judge_json(..., model=...)` — validates every attempt;
  schema failures join JSON-syntax failures in the LLM self-correction loop
  (the fix prompt quotes the validation feedback).
- `compare_chapters.compare()` — tournament verdicts.
- `gen_outline.verify_tonal_drift()` — gate verdict; a schema failure warns
  and skips the gatekeeper rather than defaulting the verdict to "clean".
- `extract_micro_plants` — per-chapter callback extraction.

**Rule:** any new judge/LLM JSON contract gets a Pydantic model here. Never
re-implement regex extraction for output that is already JSON.

Related: [llm-client.md](llm-client.md) ·
[../pipeline/scoring-engine.md](../pipeline/scoring-engine.md)
