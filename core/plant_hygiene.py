"""Outline plant hygiene: action-shaped pre-reveal beats + coverage floor.

Two inverse failures for mask/twist stories:
1. LEAK — meaning-shaped language or sealed lexicon in pre-reveal beats.
2. STARVE — target character absent from pre-reveal chapters, so a later
   retrofit has no concrete material.

Hygiene is structural: sealed-fact denylist + regex meaning frames, not
prompt hope. Offline; no LLM required for the gate.
"""

from __future__ import annotations

import re

from core import canon as canon_mod

CHAPTER_HEADER_RE = re.compile(
    r"^###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*(\d+)\b",
    re.IGNORECASE | re.MULTILINE,
)
CHARACTER_LIST_RE = re.compile(
    r"^\s*(?:\d+\.\s*)?Characters\s*:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
MEANING_FRAMES = re.compile(
    r"\b(secretly|in truth|in fact|the real|true identity|true nature|true role|"
    r"true protagonist|hidden identity|hidden agenda|hidden truth|"
    r"is actually|was actually|are actually|really (?:is|was|controls|rules)|"
    r"front for|puppet(?: master)?|mask(?:ing|ed)?|decoy|red herring|"
    r"not who (?:she|he|they) seem|was never|always was)\b",
    re.IGNORECASE,
)


def extract_chapter_blocks(outline_text: str) -> dict[int, str]:
    """Map chapter number → raw outline entry text (detailed section preferred)."""
    text = outline_text
    if "## DETAILED CHAPTER OUTLINES" in text:
        text = text.split("## DETAILED CHAPTER OUTLINES", 1)[1]
    headers = list(CHAPTER_HEADER_RE.finditer(text))
    blocks: dict[int, str] = {}
    for i, m in enumerate(headers):
        n = int(m.group(1))
        start = m.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        # Stop at next major `## ` section, starting after this header line
        # so `### Chapter` itself is not mistaken for a major section.
        header_end = start + len(m.group(0))
        next_major = re.search(r"^##(?!#) ", text[header_end:], re.MULTILINE)
        if next_major:
            end = min(end, header_end + next_major.start())
        blocks[n] = text[start:end].strip()
    return blocks


def check_pre_reveal_leaks(
    outline_text: str,
    denylist: list[str] | None = None,
    reveal_chapter: int | None = None,
) -> list[str]:
    """Return human-readable leak reports for pre-reveal chapters.

    Single tokens (names, common nouns) are too noisy: "lily" or "demon"
    appearing in a pre-reveal outline is not a leak — every chapter mentions
    the protagonist. Meaning frames and multi-word sealed phrases are the
    real signal.
    """
    if reveal_chapter is None or reveal_chapter <= 1:
        return []
    denylist = denylist or []
    problems: list[str] = []
    blocks = extract_chapter_blocks(outline_text)
    for n in sorted(blocks):
        if n >= reveal_chapter:
            continue
        body = blocks[n]
        for m in MEANING_FRAMES.finditer(body):
            problems.append(
                f"ch{n}: meaning-shaped language {m.group(0)!r} in pre-reveal beat"
            )
        low = body.lower()
        for term in denylist:
            # Only treat multi-word sealed phrases as leaks. A one-word
            # denylist entry is almost always an ordinary content word.
            if " " not in term.strip():
                continue
            if term in low:
                problems.append(
                    f"ch{n}: sealed-term {term!r} appears in pre-reveal outline"
                )
    return problems


def _chapter_characters(block: str) -> list[str]:
    m = CHARACTER_LIST_RE.search(block)
    if not m:
        return []
    raw = m.group(1)
    # Stop at next field label if the line ran long
    raw = re.split(r"\n\s*\d+\.\s+[A-Z]", raw, maxsplit=1)[0]
    parts = re.split(r"[,;]", raw)
    return [p.strip().strip("*").strip() for p in parts if p.strip().strip("*")]


def action_plant_coverage(
    outline_text: str,
    required_characters: list[str],
    reveal_chapter: int | None,
) -> dict:
    """Report how many pre-reveal chapters put each required character on the page."""
    blocks = extract_chapter_blocks(outline_text)
    if reveal_chapter is None:
        reveal_chapter = max(blocks) + 1 if blocks else 1
    pre = {n: b for n, b in blocks.items() if n < reveal_chapter}
    report: dict = {
        "reveal_chapter": reveal_chapter,
        "pre_reveal_chapters": sorted(pre),
        "required": list(required_characters),
        "per_character": {},
        "pass": True,
        "problems": [],
    }
    if not required_characters:
        return report
    if not pre:
        report["pass"] = False
        report["problems"].append("no pre-reveal chapters found in outline")
        return report
    for name in required_characters:
        present_in = []
        for n, body in pre.items():
            chars = _chapter_characters(body)
            in_list = any(name.lower() in c.lower() or c.lower() in name.lower() for c in chars)
            # Also accept agent-of-action mention outside the Characters line
            in_prose = bool(re.search(rf"\b{re.escape(name)}\b", body))
            if in_list or in_prose:
                present_in.append(n)
        report["per_character"][name] = sorted(present_in)
        if not present_in:
            report["pass"] = False
            report["problems"].append(
                f"{name!r} never appears in pre-reveal outline "
                f"(chapters {sorted(pre)}) — retrofit would have no material"
            )
    return report


def validate_outline_plant_hygiene(
    outline_text: str,
    canon_text: str,
    characters_text: str = "",
    min_presence_chapters: int = 1,
) -> tuple[bool, str, dict]:
    """Full hygiene gate. Returns (passed, error_message, sidecar_dict)."""
    parsed = canon_mod.parse_canon(canon_text)
    reveal = parsed.reveal_chapter()
    denylist = canon_mod.sealed_denylist_terms(parsed)
    required = canon_mod.action_plant_characters(parsed, characters_text)

    leaks = check_pre_reveal_leaks(outline_text, denylist, reveal)
    coverage = action_plant_coverage(outline_text, required, reveal)

    # Soft floor: required characters must appear in at least min_presence_chapters
    # pre-reveal chapters (default 1 so a total blackout fails).
    if required and coverage["pass"]:
        for name, chs in coverage["per_character"].items():
            if len(chs) < min_presence_chapters:
                coverage["pass"] = False
                coverage["problems"].append(
                    f"{name!r} only appears in {len(chs)} pre-reveal chapter(s); "
                    f"need at least {min_presence_chapters}"
                )

    sidecar = {
        "reveal_chapter": reveal,
        "denylist_terms": denylist,
        "action_plant_characters": required,
        "leaks": leaks,
        "coverage": coverage,
    }
    problems = leaks + coverage["problems"]
    if problems:
        msg = "; ".join(problems[:12])
        if len(problems) > 12:
            msg += f" (+{len(problems) - 12} more)"
        return False, msg, sidecar
    return True, "", sidecar
