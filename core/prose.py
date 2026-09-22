"""Guard against model output that is not chapter prose.

Three shapes occur in practice (the first two both present in
``projects/sir the confortable v4``):

1. A labelled block appended after the chapter — ``---`` then
   ``**Revision Notes**`` — in which the model explains its own edits.
2. A mid-paragraph derail into prompt echo and reasoning, e.g.
   ``…like it's nest. the question says: "What is the role of differential
   reinforcement in shaping behavior?"``.
3. Artifacts inside otherwise-valid prose: structural headings the model
   invented (``## BEAT 1``), a word in another writing system dropped
   mid-sentence, or U+FFFD decode damage.

Shapes 1 and 2 are cut at the point of derail, snapped back to a sentence
boundary so the surviving prose ends cleanly. A chapter whose surviving prose
falls below ``MIN_CHAPTER_WORDS`` is reported as not-a-chapter, so callers
redraft it rather than shipping a fragment. Shape 3 is *removed* by
``strip_artifacts`` without cutting the chapter: the prose around it is sound,
so the writer's slips are dropped and logged instead of the chapter being
thrown away.

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

# --- 3. structural headings the model invents inside prose ------------------
# Observed in v5 ch11/ch12/ch17: the writer turned its own beat list into
# headings (`## BEAT 1`, `**Beat 2**`) and carried them into the final
# chapters. The outline never contains these; the model adds them, and nothing
# downstream looks.
#
# Every rule here is *deliberately narrow*, because these words are also
# ordinary English. "Part 2 of my plan was to wait." and "Scene 4 was the worst
# of them." are prose paragraphs, and a pattern that merely matched the label
# deleted them whole. A line is a marker only when it reads like a label:
# styled or bare, opening with the label and a number, and carrying at most a
# short title — never a sentence.
_MARKER_LABEL = re.compile(
    r"^(?:beat|scene)[ \t]*"
    r"(?:\d+|[ivx]+|one|two|three|four|five)\b",
    re.IGNORECASE,
)
_MARKER_STYLING = re.compile(r"^[ \t]*(?:#{1,6}[ \t]*|\*\*[ \t]*)")
_MARKER_MAX_WORDS = 6
_MARKER_SENTENCE_END = re.compile(r"[.!?,;][ \t]*\**$")
# What may follow the label when a styled marker carries a short title:
# `## BEAT 3 — Morning` is a label, `**Beat 3 was the hardest**` is a sentence.
_MARKER_TITLE_SEP = re.compile(r"^[ \t]*[:\-\u2014\u2013]")
# A chapter's own heading, which is legitimate wherever it appears — most
# importantly when the model emitted a beat marker above it. Only structural
# document headings count; "## beat 1" must NOT match here, or the exemption
# would protect the very artifact this function exists to remove.
_CHAPTER_HEADING = re.compile(
    r"^[ \t]*#{1,6}[ \t]*\**\s*(?:chapter|prologue|epilogue|interlude|part)\b",
    re.IGNORECASE,
)


def looks_like_heading_marker(line: str) -> bool:
    """True when a line is a structural label the writer invented.

    Deliberately conservative: a false negative leaves a heading in the prose
    (visible, harmless), while a false positive deletes a paragraph (silent
    damage). The bar is therefore "cannot be read as a sentence", which is why
    a styled marker may only continue with a separator (a short title) and an
    unstyled one must end at the number: `## BEAT 3 — Morning` is a label,
    `**Beat 3 was the hardest**` is prose.
    """
    text = line.strip()
    if not text or len(text) > 70:
        return False
    body = _MARKER_STYLING.sub("", text).strip().strip("*").strip()
    if not _MARKER_LABEL.match(body):
        return False
    if _MARKER_SENTENCE_END.search(text):
        return False
    if len(body.split()) > _MARKER_MAX_WORDS:
        return False
    remainder = _MARKER_LABEL.sub("", body).strip()
    if not remainder:
        return True
    # A continuation is allowed only when it is introduced as a title
    # ("## Beat 3 — Morning"). Anything else after the number is wording, and
    # wording after a label is a sentence: "**Beat 3 was the hardest**".
    return bool(_MARKER_TITLE_SEP.match(remainder))

# --- 4. script slips --------------------------------------------------------
# A word in another writing system dropped mid-sentence ("seething into the
# 毛巾"). Polish letters are legitimate in this house style; CJK, Cyrillic,
# Arabic and friends are not — they are a translation artifact, never prose.
#
# Note what is NOT here: U+3000-U+303F (CJK punctuation) and U+FF00-U+FFEF
# (fullwidth/halfwidth forms). Those ranges carry punctuation — 「」『』《》【】、
# 。 — which a project may legitimately use for quoted dialogue. Stripping them
# turned 「Trust the blood.」 into unquoted prose in projects/NewFakeSaint, so
# the fix for a stray word must not eat a punctuation system with it.
_FOREIGN_SCRIPT = re.compile(
    "[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
    "\u0400-\u04ff\u0600-\u06ff\u0590-\u05ff\u0900-\u097f\u0e00-\u0e7f]+"
)

# U+FFFD (decode failure) and U+FFFE/U+FFFF (swapped byte order) are encoding
# damage, not authorial text.
_ENCODING_DAMAGE = re.compile("[\ufffd\ufffe\uffff]")

# Punctuation a space may not sit against after a removal, on either side: the
# departing word leaves "the ." and "“ is" behind unless the seam is tidied.
_NO_SPACE_AFTER = set(",.!?;:%)]}\u201d\u2019\u2026")
_OPENING_PUNCT = set("\u201c\u2018([{")

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


def strip_artifacts(text: str) -> tuple[str, list[str]]:
    """Drop structural headings, script slips, and encoding damage.

    These are artifacts the writer emits into otherwise-valid prose, so the
    chapter stays keepable — but they must never reach the chapter file,
    because everything downstream (scoring, revision, export) treats the file
    as the book. v5 shipped beat headings in three chapters and four
    foreign-script words across four more precisely because nothing looked.

    Returns `(clean_text, removed)` where `removed` is a human-readable list
    for the caller to log: this is a pure text transform, so it does not print.

    Heading protection: the first structural heading anywhere in the text is
    kept (a draft that opens with `## BEAT 1` keeps the `# Chapter 5: Title`
    below it), and when there is no heading at all the first line of body text
    is kept instead. A marker that merely happens to be first is also kept —
    for a fresh draft it is indistinguishable from a real part divider, and
    keeping one heading is the safe side of that asymmetry.
    """
    removed: list[str] = []
    lines = text.split("\n")
    # The title is the first structural heading in the chapter, wherever it
    # sits — a draft whose line 1 is `## BEAT 1` still has one further down.
    # Only if there is no heading at all does the first line of body text get
    # the same protection.
    title_line = next(
        (i for i, raw in enumerate(lines) if _CHAPTER_HEADING.match(raw)),
        next((i for i, raw in enumerate(lines) if raw.strip()), None),
    )

    kept: list[str] = []
    dropped_at: list[int] = []
    for index, raw in enumerate(lines):
        if index != title_line and looks_like_heading_marker(raw):
            dropped_at.append(index)
            removed.append(f"heading {raw.strip()[:40]!r}")
            continue
        kept.append(raw)

    if dropped_at:
        # Swallow ONE blank line per removed marker rather than collapsing
        # every run of blank lines in the chapter: an intentional scene gap
        # elsewhere in the prose is not this function's business.
        text = _rejoin_without_gaps(lines, dropped_at)
    else:
        text = "\n".join(kept)
    del kept

    foreign = _FOREIGN_SCRIPT.findall(text)
    if foreign:
        text = _remove_and_tidy(text, _FOREIGN_SCRIPT)
        removed.append(f"{len(foreign)} foreign-script run(s) {foreign[:4]}")

    damaged = len(_ENCODING_DAMAGE.findall(text))
    if damaged:
        text = _remove_and_tidy(text, _ENCODING_DAMAGE)
        removed.append(f"{damaged} encoding-damage character(s)")

    return text, removed


def _rejoin_without_gaps(lines: list[str], dropped: list[int]) -> str:
    """Rejoin lines, deleting each dropped marker plus one adjacent blank."""
    skip = set(dropped)
    for index in dropped:
        after = index + 1
        if after < len(lines) and not lines[after].strip() and after not in skip:
            skip.add(after)
        elif index and not lines[index - 1].strip() and index - 1 not in skip:
            skip.add(index - 1)
    return "\n".join(line for i, line in enumerate(lines) if i not in skip)


def _remove_and_tidy(text: str, pattern: re.Pattern) -> str:
    """Delete every match, repairing only the whitespace each hole leaves.

    A whole-text whitespace pass would also rewrite runs far from the removal —
    `projects/sir the confortable/chapters/ch_21.md` lost 43 unrelated double
    spaces to one stray word. Here every hole is patched in place, so only the
    seam moves: a space left dangling against punctuation is dropped, and
    whitespace on both sides of a hole collapses to one space.

    Patches run in reverse so the spans already computed stay valid.
    """
    matches = list(pattern.finditer(text))
    if not matches:
        return text
    for match in reversed(matches):
        start, end = match.span()
        before = text[start - 1] if start else ""
        after = text[end:end + 1]
        if before.isspace():
            if after in _NO_SPACE_AFTER:
                start -= 1                      # drop the space, keep the mark
            elif after.isspace():
                end += 1                        # one space for both sides
        elif before in _OPENING_PUNCT and after.isspace():
            end += 1                            # no space hugs an open quote
        text = text[:start] + text[end:]
    return text


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
