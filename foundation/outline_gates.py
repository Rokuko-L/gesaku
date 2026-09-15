"""Outline-level gates: act boundaries and the tonal-drift judge.

A truncated verdict is UNKNOWN, never "no drift" — the gate must not fail
open on truncation.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import TruncationError, call_llm
from core.validation import (
    OutputValidationError, TonalDriftVerdict, parse_validated,
)

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
        from core.llm import TruncationError
        from core.validation import (
            OutputValidationError, TonalDriftVerdict, parse_validated,
        )
        raw = call_llm(prompt=prompt, system="You are a meticulous book editor who outputs valid JSON only.", model_key="judge", max_tokens=2000, temperature=0.1)
        verdict = parse_validated(TonalDriftVerdict, raw, context="tonal drift")
        if verdict.has_drift and verdict.violations:
            feedback = "Tonal/Genre violations detected:\n" + "\n".join(
                f"- {v}" for v in verdict.violations)
            return True, feedback
        return False, ""
    except TruncationError:
        # A truncated judge verdict is UNKNOWN, not "no drift" — never fail open on truncation.
        raise
    except OutputValidationError as e:
        print(f"  WARN: Tonal drift verdict failed schema ({e.feedback}), "
              f"skipping gatekeeper.", file=sys.stderr)
        return False, ""
    except Exception as e:
        print(f"  WARN: Tonal drift validation call failed ({e}), skipping gatekeeper.", file=sys.stderr)
        return False, ""
