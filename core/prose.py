"""Guard against model output that is not chapter prose.

Two shapes occur in practice (both present in
``projects/sir the confortable v4``):

1. A labelled block appended after the chapter — ``---`` then
   ``**Revision Notes**`` — in which the model explains its own edits.
2. A mid-paragraph derail into prompt echo and reasoning, e.g.
   ``…like it's nest. the question says: "What is the role of differential
   reinforcement in shaping behavior?"``.

Both are cut at the point of derail, snapped back to a sentence boundary so the
surviving prose ends cleanly. A chapter whose surviving prose falls below
``MIN_CHAPTER_WORDS`` is reported as not-a-chapter, so callers redraft it rather
than shipping a fragment.

Detection is deliberately structural — it keys on *task machinery* (the prompt,
the question, the answer choices) rather than on prose style. That matters here:
the novels this pipeline writes are first person, so in-voice deliberation
("Let me recall what she said") is normal prose and must not trip the guard.
"""

from __future__ import annotations

import re

# A chapter shorter than this after stripping is a fragment, not a chapter.
# v4's chapters run ~3000 words; 300 is a conservative floor.
MIN_CHAPTER_WORDS = 300

# --- 1. trailing labelled blocks -------------------------------------------
# The heading turns up in every markdown dress the model fancies:
# "**Revision Notes**", "**REVISION NOTES:**", "### Revision Notes",
# "## Revision Notes", usually preceded by a "---" rule.
#
# The qualifier is REQUIRED. Making it optional also matched a bare "Notes"
# line — and a chapter may legitimately contain one (`## Notes` as an in-world
# heading, a scene-break rule followed by "Notes"), which silently truncated
# everything after it. Only a *revision*/*editorial*/*change*/*edit* label
# means "the model started explaining itself".
_NOTES_HEADING = re.compile(
    r"^[ \t]*(?:[-*_]{3,}[ \t]*\n+[ \t]*)?(?:#{1,6}[ \t]*)?\**[ \t]*"
    r"(?:revision|editor(?:ial)?|change|edit)[ \t]*notes\b"
    r"[ \t]*:?[ \t]*\**[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# --- 2. derail markers ------------------------------------------------------
# Strong: unambiguous task machinery. One hit is enough to cut.
_STRONG = re.compile(
    r"(?:"
    r"\bthe (?:user|prompt|instructions?) (?:says?|asks?|wants?|is asking|was)\b"
    r"|\bthe question (?:says|asks|is asking|simply asks|was)\b"
    r"|\b(?:single|multiple)[- ]choice\b"
    r"|\bthe answer (?:should|must|would|will) be\b"
    r"|\b(?:correct|right) (?:option|answer|choice|letter)\b"
    r"|\bone of the (?:letters|options|choices)\b"
    r"|\bas an ai\b"
    r"|\bmy (?:instructions|task|job) (?:are|is|was)\b"
    r"|\blet me re-?read\b"
    r")",
    re.IGNORECASE,
)

# Weak: reasoning openers a first-person narrator may legitimately use, so a
# cluster of them counts only when it is *dense* — a reasoning block inside a
# few hundred characters. Three of these spread across a 3000-word chapter is
# ordinary interiority, not a derail.
_WEAK = re.compile(
    r"(?:"
    r"\blet me (?:recall|think|consider|check)\b"
    r"|\bi (?:should|need to|have to) (?:choose|pick|select|decide)\b"
    r"|\bthe (?:passage|excerpt|text) (?:says|states|describes)\b"
    r")",
    re.IGNORECASE,
)

_WEAK_CLUSTER = 3
_WEAK_WINDOW_CHARS = 400

_SENTENCE_END = re.compile(r"[.!?][\"'\u201d\u2019)]*\s")


def _snap_to_sentence(text: str, index: int) -> int:
    """Walk back from a derail to the end of the previous complete sentence."""
    head = text[:index]
    ends = [m.end() for m in _SENTENCE_END.finditer(head)]
    if ends:
        return ends[-1]
    # No sentence boundary: fall back to the start of the current line.
    nl = head.rfind("\n")
    return nl + 1 if nl != -1 else 0


def _locate(text: str) -> tuple[int, str] | None:
    """Earliest non-prose cut, with its reason ("notes" or "derail")."""
    candidates: list[tuple[int, str]] = []

    notes = _NOTES_HEADING.search(text)
    if notes:
        candidates.append((notes.start(), "notes"))

    strong = _STRONG.search(text)
    if strong:
        candidates.append((_snap_to_sentence(text, strong.start()), "derail"))

    weak = list(_WEAK.finditer(text))
    for i in range(len(weak) - _WEAK_CLUSTER + 1):
        span = weak[i + _WEAK_CLUSTER - 1].start() - weak[i].start()
        if span <= _WEAK_WINDOW_CHARS:
            candidates.append((_snap_to_sentence(text, weak[i].start()), "derail"))
            break

    if not candidates:
        return None
    index, reason = min(candidates, key=lambda c: c[0])
    return (index, reason) if index > 0 else None


def find_non_prose_start(text: str) -> int | None:
    """Index where non-prose begins, or None if the text is clean prose."""
    found = _locate(text)
    return found[0] if found else None


def cut_reason(text: str) -> str | None:
    """Why the text was cut: "notes" (harmless trailer) or "derail" (truncation).

    Callers treat these differently — an appended notes block can simply be
    dropped, whereas a derail means the chapter itself was interrupted.
    """
    found = _locate(text)
    return found[1] if found else None


def _trim_tail(text: str) -> str:
    lines = text.rstrip().split("\n")
    while lines and (
        not lines[-1].strip()
        or re.fullmatch(r"[-*_]{3,}", lines[-1].strip())
        or re.fullmatch(
            r"#{1,6}[ \t]*\**\s*(?:revision|edit|change)?\s*notes?\s*:?\s*\**",
            lines[-1].strip(), re.IGNORECASE,
        )
    ):
        lines.pop()
    return "\n".join(lines).rstrip()


def strip_non_prose(text: str) -> str:
    """Return only the prose, dropping any derail or appended notes block."""
    cut = find_non_prose_start(text)
    if cut is None:
        return text.strip()
    return _trim_tail(text[:cut])


def prose_words(text: str) -> int:
    """Word count of the text after stripping."""
    return len(strip_non_prose(text).split())


def looks_like_non_prose(text: str, min_words: int = MIN_CHAPTER_WORDS) -> bool:
    """True when too little prose survives to be a chapter.

    Signals either a wholly non-prose body or a derail so early that the
    remainder is a fragment. Suitable for *reporting* (export warns with it).

    Not suitable as a hard gate on a fresh draft: a deliberate interlude is
    legitimately short, and `pipeline/phases/drafting.py` already has an
    undershoot path that retries with concrete expansion numbers. Use
    `needs_redraft` for the reject decision.
    """
    return prose_words(text) < min_words


def needs_redraft(text: str, min_words: int = MIN_CHAPTER_WORDS) -> bool:
    """True when a chapter was *interrupted*, not merely written short.

    A derail means the model stopped writing the story and started talking
    about the task; if almost nothing survives the cut, there is no chapter to
    keep. Nothing else is rejected on length.
    """
    return cut_reason(text) == "derail" and prose_words(text) < min_words
