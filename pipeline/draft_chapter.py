#!/usr/bin/env python3
"""
Draft a single chapter using the writer model.
Usage: python draft_chapter.py 1
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import TruncationError, call_llm
from core.outline import parse_premise_beats, normalize_chapter_heading
from core.paths import get_novel_title
from core.textstats import check_structural_repetition
from core import canon as canon_mod
import json
import re
import sys
from pathlib import Path
from dotenv import load_dotenv
from core.genre import load_genre, prose_mode_system_block
from core import paths
from core import textstats

load_dotenv()

def call_writer(prompt, max_tokens=None):
    genre_cfg = load_genre()
    chapter_system = genre_cfg["identity"]["chapter_system"]
    perspective = genre_cfg.get("perspective", "")
    if perspective:
        if perspective == "first_person":
            chapter_system += ("\n\nMANDATORY PERSPECTIVE: Write this chapter in STRICT FIRST-PERSON "
                               "limited narration from the POV character ('I/me/my'). The POV "
                               "character narrates everything; no third-person narration anywhere.")
        else:
            chapter_system += ("\n\nMANDATORY PERSPECTIVE: Write this chapter in STRICT THIRD-PERSON "
                               "limited narration anchored to the POV character ('he/she/they' or the "
                               "character's name). Never switch to first-person narration.")
    chapter_system += prose_mode_system_block(genre_cfg)
    estimated_words = genre_cfg["generation"]["outline"]["estimated_words"]
    chapter_count = genre_cfg["generation"]["outline"]["estimated_chapters"]
    target_words = estimated_words // chapter_count
    prompt_target_words = int(target_words * 1.35)  # inflate prompt target so LLM undershoot lands near real target
    # Cap output tokens at ~3.25x target word count to prevent runaway generation
    if max_tokens is None:
        max_tokens = int(target_words * 3.25)
    system_prompt = chapter_system + f"\n\nWRITING REQUIREMENT: This chapter must be approximately {prompt_target_words} words. Write fully, expansively, and completely to hit this target. Flesh out every scene with sensory details, full dialogues, and deep character interiority. Avoid summarizing events, skipping actions, or rushing through the narrative. Pacing should be slow, detailed, and immersive."
    return call_llm(prompt=prompt, system=system_prompt, model_key="writer", max_tokens=max_tokens, beta_context=True, timeout=600, temperature=0.8, raise_on_truncation=True)

def load_file(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""

def extract_chapter_outline(outline_text, chapter_num):
    """Extract a specific chapter's outline entry from the DETAILED section.

    Scoped to '## DETAILED CHAPTER OUTLINES' so the HIGH-LEVEL ROADMAP one-liner
    (which appears earlier in the file) is never matched instead of the real
    beats entry. Raises if the entry is missing — a chapter drafted without its
    outline is worse than no draft at all.
    """
    if "## DETAILED CHAPTER OUTLINES" in outline_text:
        # Scope to the detailed section: a fresh outline's HIGH-LEVEL ROADMAP
        # one-liner appears first and must never be drafted from instead of the
        # real beats entry.
        outline_text = outline_text.split("## DETAILED CHAPTER OUTLINES", 1)[1]
    # Rebuilt outlines (post-export, "### Ch N:" format) have no roadmap and no
    # DETAILED header — whole-text search is correct for them.
    pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{chapter_num}\b.*?(?=###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*(?:\d+)\b|## Act|## Foreshadowing|$)'
    match = re.search(pattern, outline_text, re.IGNORECASE | re.DOTALL)
    if not match:
        raise ValueError(
            f"Chapter {chapter_num} outline entry not found in the "
            f"## DETAILED CHAPTER OUTLINES section — refusing to draft without beats."
        )
    return match.group(0).strip()

def extract_next_chapter_outline(outline_text, chapter_num):
    """Extract the next chapter's outline (just first few lines for continuity)."""
    try:
        next_entry = extract_chapter_outline(outline_text, chapter_num + 1)
    except ValueError:
        return "(final chapter)"
    lines = next_entry.split('\n')[:10]
    return '\n'.join(lines)

def scan_prior_chapter_crutches(chapters_dir, current_chapter, max_phrases=12):
    """Find distinctive phrases used across PRIOR chapters and warn against reuse.

    Cross-chapter crutch memory: an image or metaphor is atmospheric once,
    but repeating "bruise-colored sky" in 4 chapters reads as a machine
    tick. Extract distinctive (rare-word) 3-4 gram phrases from all prior
    chapters, count how many chapters they appear in, and return the worst
    offenders as a do-not-reuse list.
    """
    import collections
    stop = {"the", "a", "an", "and", "or", "but", "of", "in", "on", "at",
                "to", "for", "with", "from", "by", "her", "his", "she", "he",
                "it", "was", "were", "had", "have", "has", "as", "so", "if",
                "then", "that", "this", "there", "her", "their", "its", "not",
                "no", "yes", "you", "your", "they", "them", "we", "our", "my",
                "me", "i", "said", "says", "would", "could", "should", "will",
                "can", "may", "might", "must", "been", "being", "herself",
                "himself", "itself", "down", "up", "out", "off", "over",
                "under", "again", "more", "most", "some", "any", "all", "few",
                "both", "each", "every", "own", "same", "too", "very", "just",
                "into", "onto", "about", "after", "before", "between",
                "through", "during", "without", "against", "around", "within",
                "along", "across", "behind", "beyond", "beneath", "among"}

    phrase_counts = collections.defaultdict(set)
    for ch in range(1, current_chapter):
        path = chapters_dir / f"ch_{ch:02d}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        words = text.split()
        for n in (4, 3):
            for i in range(len(words) - n + 1):
                gram = words[i:i + n]
                if any(w.strip(".,;:!?\"'()[]-—").lower() in stop for w in gram):
                    continue
                key = " ".join(w.strip(".,;:!?\"'()[]-—") for w in gram).lower()
                if len(key) < 12:
                    continue
                phrase_counts[key].add(ch)

    # Phrases appearing in 2+ distinct prior chapters are crutches
    crutches = [(p, sorted(chs)) for p, chs in phrase_counts.items() if len(chs) >= 2]
    crutches.sort(key=lambda x: -len(x[1]))
    return crutches[:max_phrases]


def parse_orientation_facts(chapter_outline):
    import re
    match = re.search(r'Orientation\s+Facts\s*(?:\*\*)?:\s*(.*?)(?=\n\s*(?:-\s*)?\*\*|\Z)', chapter_outline, re.IGNORECASE | re.DOTALL)
    if match:
        facts = []
        for line in match.group(1).splitlines():
            line = line.strip().lstrip('-*').strip()
            if line:
                facts.append(line)
        return facts
    return []

def main():
    chapter_num = int(sys.argv[1])
    retry_feedback = ""
    for i, arg in enumerate(sys.argv[2:]):
        if arg == "--retry-feedback" and i + 2 < len(sys.argv):
            fb_arg = sys.argv[i + 3]
            fb_path = Path(fb_arg)
            if fb_path.exists():
                retry_feedback = fb_path.read_text(encoding="utf-8")
            else:
                retry_feedback = fb_arg
            break
    
    # Load all context
    voice = load_file(paths.get_voice_path())
    world = load_file(paths.get_world_path())
    characters = load_file(paths.get_characters_path())
    outline = load_file(paths.get_outline_path())
    canon_text = load_file(paths.get_canon_path())
    # Chapter-scoped writer view: public foundation + core + prior As-of only.
    # Sealed foundation (visible_from > chapter_num) is structurally excluded.
    canon_view = canon_mod.writer_view_md(canon_mod.parse_canon(canon_text), chapter_num)
    
    # Chapter-specific context
    chapter_outline = extract_chapter_outline(outline, chapter_num)
    next_chapter = extract_next_chapter_outline(outline, chapter_num)
    
    # Check for active narrative debts to resolve in this chapter
    chapter_harvests = re.findall(r'\[Harvest:\s*([a-zA-Z0-9_-]+)', chapter_outline, re.IGNORECASE)
    active_debts_to_resolve = []
    if chapter_harvests:
        try:
            state_path = paths.get_project_dir() / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            debts = state.get("debts", [])
            for h_slug in chapter_harvests:
                h_slug_clean = h_slug.strip().lower()
                for d in debts:
                    if h_slug_clean in d.lower():
                        active_debts_to_resolve.append(d)
        except Exception:
            pass

    debt_guardrail = ""
    if active_debts_to_resolve:
        debt_lines = "\n".join(f"- {d}" for d in active_debts_to_resolve)
        debt_guardrail = f"""
NARRATIVE DEBTS RESOLUTION WARNING:
This chapter is scheduled to pay off the following narrative setup(s):
{debt_lines}
You MUST write prose in this chapter that resolves these setups naturally.
"""
    
    # Previous chapter (if exists) — full ~600-word tail starting at a sentence boundary
    chapters_dir = paths.get_chapters_dir()
    prev_path = chapters_dir / f"ch_{chapter_num - 1:02d}.md"
    if prev_path.exists():
        prev_text = prev_path.read_text(encoding="utf-8")
        prev_tail = textstats.tail_context(prev_text, max_words=600)
    else:
        prev_tail = "(first chapter -- no previous)"

    title = get_novel_title()

    # Build structural guardrails (applied to EVERY chapter)
    structural_guardrails = """
STRUCTURAL RULES (apply to every chapter):
- If a scene involves a list (multiple rules, observations, items), present them
  together in ONE consolidated scene. Do NOT repeat the surrounding scene-setting
  (checking a UI, looking at a calendar, etc.) for each sub-item.
- Each beat should introduce content that hasn't appeared in an earlier beat.
  Do not have the character re-discover, re-read, or re-react to the same object,
  document, or realization in more than one beat.
- The reader must be grounded at the start of this chapter. Every name, title,
  location, and relationship must be established through events — not assumed.

SCORING RULE — STACCATO PENALTY:
- Every paragraph with 3+ consecutive sentences of ≤4 words each triggers a -0.5 penalty.
- This penalty stacks per-paragraph and is NOT capped — it can destroy your score.
- BAD: "He nodded. She smiled. It was nothing." (3 consecutive short = 1 penalty instance)
- BAD: "I waited. He didn't move. The silence stretched. Awkward." (4 consecutive = 1 instance)
- GOOD: "He nodded, but the smile didn't reach his eyes — it was a performance we both saw through."
- Vary sentence lengths naturally. Every paragraph should have a blend of short, medium, and long sentences.
"""
    if debt_guardrail:
        structural_guardrails += "\n" + debt_guardrail

    # Per-beat word budgets. Without concrete numbers the writer front-loads
    # early beats and compresses the rest — observed: 15 consecutive Chapter-1
    # drafts below the word floor until explicit per-beat budgets were added.
    _genre_cfg = load_genre()
    _target_words = (_genre_cfg["generation"]["outline"]["estimated_words"]
                     // max(_genre_cfg["generation"]["outline"]["estimated_chapters"], 1))
    _n_beats = len(re.findall(r'(?m)^\s+\d+[\.\)]\s+', chapter_outline))
    if _n_beats >= 3:
        _per_beat = max(200, int(_target_words / _n_beats))
        structural_guardrails += f"""

WORD BUDGET BY BEAT:
This chapter has {_n_beats} scene beats and must total ~{_target_words} words.
Budget roughly {_per_beat} words PER beat — full scene treatment each, not a
paragraph. Do not compress later beats; if you are running long, deepen
sensory detail and interiority rather than cutting beats.
"""

    # Parse and append Orientation Facts checklist to guardrails
    orientation_facts = parse_orientation_facts(chapter_outline)
    if orientation_facts:
        fact_lines = "\n".join(f"  - {f}" for f in orientation_facts)
        orientation_guardrail = f"""
ORIENTATION CHECKLIST (MUST DRAMATIZE):
You MUST explicitly establish and dramatize the following facts in the chapter prose. Ground them in what is happening externally and internally. Do NOT simply state them in a summary narration block; dramatize them through action, thoughts, or dialogue:
{fact_lines}
"""
        structural_guardrails += "\n" + orientation_guardrail

    # Chapter 1 premise-beat guardrail — enumerate beats from the validated outline
    premise_guardrail = ""
    if chapter_num == 1:
        beats = parse_premise_beats(outline)
        if beats:
            beat_lines = "\n".join(
                f"  {i+1}. {b['beat']} — {b['scene_summary']}"
                for i, b in enumerate(beats)
            )
            premise_guardrail = f"""
CHAPTER 1 READER ORIENTATION:
Your outline for this chapter contains these required premise-establishment
beats, in order. You MUST draft prose for each beat before moving to the
chapter's main plot scenes. Each beat gets real scene treatment — do not
compress, skip, or summarize a beat in a single sentence.

PREMISE BEATS:
{beat_lines}

CRITICAL: These beat names (e.g. "ordinary_world", "observer_reveal") are
internal labels for your planning only. Do NOT print them, bold them,
reference them, or use them as section headers in the chapter output.
Write continuous prose with no section breaks between beats — the transition
between beats should be a natural prose transition, not a labeled divider.
"""
        # Check premise validation flag
        prem_val_path = paths.get_project_dir() / "premise_validation.json"
        if prem_val_path.exists():
            prem_val = json.loads(prem_val_path.read_text(encoding="utf-8"))
            if not prem_val.get("passed"):
                premise_guardrail += (
                    "\nNOTE: This chapter's outline did not pass automated premise-beat "
                    "validation. The beats listed above may be incomplete or out of order. "
                    "Weigh reader-grounding with extra scrutiny — ensure every concept is "
                    "properly introduced on the page.\n"
                )
    prompt = f"""Write Chapter {chapter_num} of "{title}."

VOICE DEFINITION (follow this exactly):
{voice}

THIS CHAPTER'S OUTLINE (hit every beat):
{chapter_outline}

NEXT CHAPTER'S OUTLINE (for continuity -- end this chapter so it flows into the next):
{next_chapter}

PREVIOUS CHAPTER'S ENDING (continue from here):
{prev_tail}

WORLD BIBLE (reference for worldbuilding details):
{world}

CHARACTER REGISTRY (reference for speech patterns and behavior):
{characters}
"""

    if canon_view:
        prompt += f"""
CANON AS OF THIS CHAPTER (facts the reader may already know; anything else must
be introduced through this chapter's events — not assumed, not name-dropped).
Sealed author truths are intentionally withheld and must not be invented or hinted:
{canon_view}
"""

    # Cross-chapter crutch memory: ban distinctive phrases already overused
    # in prior chapters (a phrase that appears in 2+ earlier chapters).
    crutches = scan_prior_chapter_crutches(chapters_dir, chapter_num)
    if crutches:
        crutch_lines = "\n".join(
            f"  - \"{p}\" (used in chapters {', '.join(str(c) for c in chs)})"
            for p, chs in crutches)
        structural_guardrails += f"""

CROSS-CHAPTER CRUTCH BAN (READ CAREFULLY):
The following distinctive phrases were already used repeatedly in EARLIER chapters.
Do NOT use them, or close variants of them, in this chapter. Find a fresh image
or metaphor each time:
{crutch_lines}
"""

    if retry_feedback:
        structural_guardrails += f"""

EVALUATOR FEEDBACK FROM PREVIOUS ATTEMPT (address EVERY point):
{retry_feedback}
"""

    prompt += f"""
WRITING INSTRUCTIONS:
{load_genre()["generation"]["draft_chapter_instructions"]}

{structural_guardrails}
{premise_guardrail}
FORMATTING:
Start the chapter with a single markdown H1 title line, exactly:
`# Chapter {chapter_num}: <Chapter Title>`
Nothing else on that line — no bold, no "##", no slug/codename.
Write the chapter now. Full text, beginning to end.
"""

    MAX_REP_ATTEMPTS = 2
    MAX_TRUNC_RETRIES = 2
    repetition_feedback = ""
    genre_cfg = load_genre()
    _est_words = genre_cfg["generation"]["outline"]["estimated_words"]
    _chapter_count = genre_cfg["generation"]["outline"]["estimated_chapters"]
    target_words = _est_words // _chapter_count
    max_tokens = None
    trunc_retries_left = MAX_TRUNC_RETRIES
    result = None

    for attempt in range(1, MAX_REP_ATTEMPTS + 1):
        print(f"Drafting Chapter {chapter_num} (regen check {attempt}/{MAX_REP_ATTEMPTS})...", file=sys.stderr)
        try:
            result = call_writer(prompt + repetition_feedback, max_tokens=max_tokens)
        except TruncationError as e:
            if trunc_retries_left > 0:
                trunc_retries_left -= 1
                # Grow the budget ONCE to a hard ceiling instead of compounding
                # 1.5x indefinitely. Unbounded growth licensed runaway chapters
                # (observed: 11,005 words for a 3,200 target -> length penalty
                # 5.25 destroyed an otherwise 7.0-raw chapter). Ceiling ~3.9x
                # target still permits a full-length chapter plus margin.
                base = int(target_words * 3.25) if max_tokens is None else max_tokens
                max_tokens = min(int(base * 1.2), int(target_words * 3.9))
                # Tell the writer it over-ran and must compress to target.
                repetition_feedback = (
                    "\n\nTRUNCATION FIX REQUIRED: your previous attempt was cut off "
                    f"before finishing (target ~{int(target_words * 1.15)} words). "
                    "The chapter ran too long. Rewrite it tighter: hit the target "
                    "word count, finish all outline beats, and end decisively."
                )
                print(f"TRUNCATION_DETECTED: {e} — retrying with max_tokens={max_tokens} "
                      f"({trunc_retries_left} retries left)", file=sys.stderr)
                continue
            print(f"TRUNCATION_DETECTED: {e}", file=sys.stderr)
            sys.exit(2)

        rep_regen, rep_feedback, rep_sidecar = check_structural_repetition(result)

        # Write sidecar
        rep_path = chapters_dir / "repetition_check.json"
        rep_path.write_text(json.dumps(rep_sidecar, indent=2), encoding="utf-8")

        if not rep_regen or attempt == MAX_REP_ATTEMPTS:
            break

        # Build targeted feedback for regen
        repetition_feedback = "\n\nREPETITION FIX REQUIRED:\n" + "\n".join(rep_feedback)
        print(f"  Structural repetition detected — retrying...", file=sys.stderr)

    if result is None:
        # Every rep attempt ended in truncation (each 'continue' burned a retry
        # but never reached sys.exit) — never reach the save with no draft.
        print("TRUNCATION_DETECTED: all attempts truncated — no draft produced", file=sys.stderr)
        sys.exit(2)

    # Save
    out_path = chapters_dir / f"ch_{chapter_num:02d}.md"
    out_path.write_text(normalize_chapter_heading(result, chapter_num), encoding="utf-8")
    print(f"Saved to {out_path}", file=sys.stderr)
    print(f"Word count: {len(result.split())}", file=sys.stderr)
    print(result)

if __name__ == "__main__":
    main()
