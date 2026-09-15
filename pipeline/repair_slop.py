#!/usr/bin/env python3
"""Targeted mechanical-slop repair for a single chapter (JSON-patch protocol).

When a chapter's raw judge score is good but the mechanical slop detector
(staccato runs, "not X but Y", stacked negation, x_of_y frames, etc.) drags
the final score below the keep bar, blind regeneration wastes attempts and
often regresses content quality. Instead, repair IN PLACE:

1. LOCAL PRE-PASS (deterministic, zero LLM cost): mechanically fix the
   trivially-safe patterns — whole-paragraph staccato runs get merged into
   single compound sentences. Only the residue goes to the LLM.
2. JSON CONTRACT: send the LLM a mapping of paragraph IDs to their exact
   original text plus the offending patterns; it returns a JSON object
   {id: rewritten_text}. No inline markers in prose to corrupt.
3. STRUCTURAL VERIFICATION (stronger than marker round-trips):
   - every requested ID present, no extra IDs
   - no empty replacements
   - no no-op replacements (rewritten == original means the LLM dodged)
   - length ratio sanity check (blocks truncation or runaway expansion)
4. CONTENT-ANCHORED SPLICE: replace each original paragraph by locating
   its exact text in the chapter — the caller owns the anchors, so the
   wrong paragraph can never be patched.
5. LOCAL RE-SCORE GATE: rerun the deterministic detector on the patched
   chapter BEFORE any judge call; accept only if the mechanical penalty
   actually dropped. This keeps the loop self-verifying without spending
   judge tokens on failed repairs.
6. ITERATIVE: up to 2 LLM passes, each repairing what remains flagged.

Usage: python repair_slop.py <chapter_number>
Exit code 0 = repair applied and gate passed (caller should re-evaluate);
1 = nothing to repair or repair failed (caller falls back to regen).
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core import llm
from core import paths
import re
import sys
from pathlib import Path

from core import _utf8
from pipeline.evaluate import (
    PROSE_TIC_PATTERNS,
    FICTION_AI_TELLS,
    STRUCTURAL_AI_TICS,
    TIER1_BANNED,
    TIER2_SUSPICIOUS,
    TIER3_FILLER,
    TRANSITION_OPENERS,
    slop_score,
)

MAX_LLM_PASSES = 2

ALL_PATTERNS = (
    [("prose_tic", name, pat) for name, pat in PROSE_TIC_PATTERNS]
    + [("fiction_tell", f"fiction#{k}", pat) for k, pat in enumerate(FICTION_AI_TELLS)]
    + [("structural_tic", f"structural#{k}", pat) for k, pat in enumerate(STRUCTURAL_AI_TICS)]
    + [("tier3", f"tier3#{k}", pat) for k, pat in enumerate(TIER3_FILLER)]
)


def split_paragraphs(text: str):
    """Return list of (start_line_1based, end_line_1based_inclusive, para_text)."""
    lines = text.split("\n")
    paras = []
    cur_start = None
    buf = []
    for i, line in enumerate(lines):
        if line.strip():
            if cur_start is None:
                cur_start = i + 1
            buf.append(line)
        else:
            if cur_start is not None:
                paras.append((cur_start, i, "\n".join(buf)))
                cur_start = None
                buf = []
    if cur_start is not None:
        paras.append((cur_start, len(lines), "\n".join(buf)))
    return paras


def scan_paragraph(para_text: str):
    """Return list of (pattern_label, matched_phrase) found in a paragraph."""
    hits = []
    for label, name, pattern in ALL_PATTERNS:
        for m in re.finditer(pattern, para_text, re.IGNORECASE):
            snippet = para_text[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
            hits.append((f"{label}:{name}", snippet.strip()))
    for w in TIER1_BANNED:
        if re.search(rf"\b{re.escape(w)}\b", para_text, re.IGNORECASE):
            hits.append(("tier1", w))
    for w in TIER2_SUSPICIOUS:
        if re.search(rf"\b{re.escape(w)}\b", para_text, re.IGNORECASE):
            hits.append(("tier2", w))
    return hits


def staccato_paragraph(para_text: str) -> int:
    """Count staccato runs (3+ consecutive sentences <=4 words) in a paragraph."""
    para_clean = para_text.replace("...", " ").replace("..", " ")
    sents = [s.strip() for s in re.split(r"[.!?]+", para_clean) if any(c.isalnum() for c in s)]
    runs = 0
    run = 0
    for s in sents:
        if len(s.split()) <= 4:
            run += 1
        else:
            if run >= 3:
                runs += run - 2
            run = 0
    if run >= 3:
        runs += run - 2
    return runs


def transition_opener_paragraph(para_text: str) -> bool:
    first = para_text.split()[0].lower().strip(".,;:!?\"'()") if para_text.split() else ""
    return first in TRANSITION_OPENERS


def flag_paragraphs(text: str):
    """Return list of (start_line, end_line, para_text, reasons) with any slop."""
    flagged = []
    for start_line, end_line, para_text in split_paragraphs(text):
        if start_line == 1 and para_text.lstrip().startswith("#"):
            continue  # markdown title header, not prose
        reasons = []
        stacc = staccato_paragraph(para_text)
        if stacc >= 1:
            reasons.append((f"staccato_runs:{stacc}", f"{stacc} run(s) of 3+ consecutive short sentences"))
        if transition_opener_paragraph(para_text):
            reasons.append(("transition_opener", "paragraph starts with a transition word"))
        reasons += scan_paragraph(para_text)
        if reasons:
            flagged.append((start_line, end_line, para_text, reasons))
    return flagged


def fix_staccato_only_paragraph(para_text: str) -> str:
    """Deterministic fix: if the WHOLE paragraph is one staccato run (every
    sentence <=4 words, all plain declaratives, no dialogue), merge the
    sentences into one compound sentence. Conservative on purpose — anything
    risky is left for the LLM pass."""
    para = para_text.strip()
    if not para or '"' in para or "'" in para:
        return para_text
    parts = re.split(r"(?<=[.!?])\s+", para)
    if len(parts) < 3:
        return para_text
    for p in parts:
        if not p.endswith("."):
            return para_text
        wc = len(p.rstrip(".").split())
        if wc > 4 or wc < 2:
            return para_text  # single words ("A. B. C.") are emphatic, not staccato
    joined = ", ".join(p.rstrip(".") for p in parts)
    return joined + "."


def deterministic_prepass(text: str):
    """Apply local fixes paragraph by paragraph; return (new_text, n_fixed)."""
    lines = text.split("\n")
    fixed = 0
    for start_line, end_line, para_text in split_paragraphs(text):
        if start_line == 1 and para_text.lstrip().startswith("#"):
            continue
        new = fix_staccato_only_paragraph(para_text)
        if new != para_text:
            lines[start_line - 1:end_line] = new.split("\n")
            fixed += 1
    return "\n".join(lines), fixed


def local_mech_penalty(text: str) -> float:
    """Mechanical slop penalty exactly as the evaluator computes it."""
    s = slop_score(text)
    return (s.get("slop_penalty", 0) or 0) + (s.get("prose_tic_penalty", 0) or 0)


def build_patch_prompt(flagged: list, chapter_num: int, title_line: str) -> str:
    blocks = []
    for i, (start_line, _, para_text, reasons) in enumerate(flagged, start=1):
        reason_lines = "\n".join(f"  - {lab}: '{ph}'" for lab, ph in reasons[:6]) or "  - mechanical pattern"
        blocks.append(
            f'p{i} (lines {start_line}-?):\n{para_text}\nPATTERNS:\n{reason_lines}'
        )
    return f"""You are a surgical prose editor. Rewrite the following paragraphs from Chapter {chapter_num} ("{title_line}") of a novel to remove the listed mechanical AI-slop patterns (staccato runs, "not X, but Y", stacked negation, "the X of Y" abstract frames, tier words, etc.).

Return a SINGLE JSON object mapping each paragraph ID to its rewritten text, e.g.:
{{"p1": "rewritten paragraph one", "p2": "rewritten paragraph two"}}

HARD RULES:
- Every paragraph ID above MUST appear as a key. No extra keys.
- The rewritten text MUST differ from the original (no unchanged paragraphs).
- Preserve meaning, scene content, emotional register, characters, dialogue, and roughly the same length. Tighten only where the pattern demanded it.
- Convert banned constructions to natural, varied prose: positive statements instead of stacked negation; concrete detail instead of 'the X of Y' frames; break staccato runs by merging or expanding.
- JSON only — no markdown fences, no commentary, no keys beyond the IDs.

PARAGRAPHS:
{chr(10).join(blocks)}
"""


def parse_and_verify(raw: str, originals: dict):
    """Parse the LLM JSON and structurally verify it. Returns (data, error)."""
    from core.validation import OutputValidationError, SlopRepairPatch, parse_validated
    try:
        data = dict(parse_validated(
            SlopRepairPatch, raw, context="slop repair patch"
        ).root)
    except OutputValidationError as e:
        return None, f"schema failure: {e.feedback}"
    except Exception as e:
        return None, f"unparseable JSON: {e}"

    ids = set(originals.keys())
    missing = ids - set(data.keys())
    extra = set(data.keys()) - ids
    if missing or extra:
        return None, f"ID mismatch — missing {sorted(missing)}, extra {sorted(extra)}"

    for pid, orig in originals.items():
        new = data.get(pid)
        if new.strip() == orig.strip():
            return None, f"no-op replacement for {pid} (unchanged text)"
        ratio = len(new) / max(len(orig), 1)
        if ratio < 0.3 or ratio > 3.5:
            return None, f"suspicious length ratio for {pid}: {ratio:.1f}x"
    return data, None


def splice_by_content(text: str, flagged: list, data: dict) -> str:
    """Replace each flagged paragraph by locating its EXACT original text."""
    result = text
    for i, (_, _, para_text, _) in enumerate(flagged, start=1):
        pid = f"p{i}"
        idx = result.find(para_text)
        if idx == -1:
            raise ValueError(f"anchor paragraph lost for {pid}")
        result = result[:idx] + data[pid] + result[idx + len(para_text):]
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python repair_slop.py <chapter_number>", file=sys.stderr)
        sys.exit(1)
    chapter_num = int(sys.argv[1])

    chapters_dir = paths.get_chapters_dir()
    ch_path = chapters_dir / f"ch_{chapter_num:02d}.md"
    if not ch_path.exists():
        print(f"ERROR: {ch_path} not found", file=sys.stderr)
        sys.exit(1)
    text = ch_path.read_text(encoding="utf-8")
    title_line = text.strip().split("\n")[0].lstrip("#* ").strip()

    # Stage 1: deterministic pre-pass
    text, n_pre = deterministic_prepass(text)
    if n_pre:
        print(f"PREPASS Chapter {chapter_num}: {n_pre} paragraph(s) fixed locally", file=sys.stderr)

    # Stage 2: iterative LLM passes (capped)
    total_llm = 0
    for pass_no in range(1, MAX_LLM_PASSES + 1):
        flagged = flag_paragraphs(text)
        if not flagged:
            break
        originals = {f"p{i}": para for i, (_, _, para, _) in enumerate(flagged, start=1)}
        before = local_mech_penalty(text)
        print(f"REPAIR PASS {pass_no} Chapter {chapter_num}: {len(flagged)} flagged paragraph(s)", file=sys.stderr)
        for start_line, _, _, reasons in flagged:
            print(f"  lines {start_line}: {', '.join(lab for lab, _ in reasons[:4])}", file=sys.stderr)

        prompt = build_patch_prompt(flagged, chapter_num, title_line)
        raw = llm.call_llm(
            prompt=prompt,
            model_key="writer",
            max_tokens=6000,
            temperature=0.6,
            timeout_role="standard",
        )
        if not raw or len(raw.strip()) < 20:
            print("REPAIR_FAILED empty LLM response", file=sys.stderr)
            sys.exit(1)

        data, err = parse_and_verify(raw, originals)
        if err:
            print(f"REPAIR_FAILED {err}", file=sys.stderr)
            sys.exit(1)

        try:
            new_text = splice_by_content(text, flagged, data)
        except ValueError as e:
            print(f"REPAIR_FAILED {e}", file=sys.stderr)
            sys.exit(1)

        after = local_mech_penalty(new_text)
        print(f"  local mech penalty: {before:.2f} -> {after:.2f}", file=sys.stderr)
        if after >= before:
            print("REPAIR_FAILED local gate: slop penalty did not drop", file=sys.stderr)
            sys.exit(1)

        text = new_text
        total_llm += len(flagged)

    if n_pre == 0 and total_llm == 0:
        print(f"NO_SLOP Chapter {chapter_num} — nothing to repair", file=sys.stderr)
        sys.exit(0)

    ch_path.write_text(text, encoding="utf-8")
    print(f"REPAIRED Chapter {chapter_num}: {n_pre} pre-pass + {total_llm} LLM paragraph(s)", file=sys.stderr)
    print(f"Word count: {len(text.split())}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
