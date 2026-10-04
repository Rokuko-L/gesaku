"""Host-scoped retrieval packs for draft/revision prompts.

Python decides what the writer may see, then generation stays one-shot.
Modes (GESAKU_RETRIEVAL_MODE): `dump` (full bibles — safe default) or
`scoped` (beat-scoped sections). Sealed canon never enters writer packs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

STOPWORDS = frozenset(
    """a an and are as at be but by for from has have had he her his him i it its
    is was were will with without this that these those there here when while
    who whom which what why how all any both each few more most other some such
    not no yes then than too very just also into onto about under over again
    chapter chapters scene scenes act beat beats outline previous next world
    bible character characters registry voice definition canon sealed author
    must may might can could should would if or nor so if of on in to do does
    did done have has had role age focus area pronouns personality flaws fears
    relationships costs actions notices patterns""".split()
)

# Named section budgets — quality knobs, not call-site literals.
MAX_CHARACTER_SECTIONS = 6
MAX_WORLD_SECTIONS = 4
# A "scoped" hit that is essentially the whole file is not scoping.
FULLTEXT_HIT_RATIO = 0.8

# Title Case or ALL-CAPS name runs (registry headings often shout names).
# `[ \t]+` — never `\s+` (newlines must not glue a chapter title to the next
# sentence into a fake entity). Same rule as core.continuity_text.
PROPER_NOUN_RE = re.compile(
    r"\b([A-Z]{2,}(?:[ \t]+[A-Z]{2,})*|[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)*)\b"
)
HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
MD_NOISE_RE = re.compile(r"[*_`\"]+")
SECTION_LABEL_RE = re.compile(
    r"^(?:[A-Z]{1,4}\.|\d+\.|[IVXLC]+\.)*\s*(?:THE\s+)?(.+)$",
    re.IGNORECASE,
)
ALLCAPS_NOISE = frozenset(
    """THE AND FOR WITH FROM THAT THIS ROLE AGE FOCUS AREA NARRATOR SUPPORTING
    CHARACTERS CHARACTER REGISTRY WORLD BIBLE VOICE DEFINITION""".split()
)


@dataclass
class RetrievalPack:
    """Scoped (or dump) context blocks for one chapter prompt."""

    mode: str
    characters_block: str = ""
    world_block: str = ""
    canon_view: str = ""
    query_terms: list[str] = field(default_factory=list)
    character_hits: list[str] = field(default_factory=list)
    world_hits: list[str] = field(default_factory=list)
    character_fallback: bool = False
    world_fallback: bool = False

    def to_telemetry(self) -> dict:
        return {
            "mode": self.mode,
            "query_terms": list(self.query_terms),
            "character_hits": [h for h in self.character_hits if not str(h).startswith("(fallback")],
            "world_hits": [h for h in self.world_hits if not str(h).startswith("(fallback")],
            "character_fallback": self.character_fallback,
            "world_fallback": self.world_fallback,
            "characters_chars": len(self.characters_block),
            "world_chars": len(self.world_block),
            # Full draft prompt size lives in llm_events.jsonl; not known here.
            "prompt_chars_before": None,
            "prompt_chars_after": None,
        }


def retrieval_mode() -> str:
    """Resolve GESAKU_RETRIEVAL_MODE. Unknown values fall back to dump."""
    raw = (os.getenv("GESAKU_RETRIEVAL_MODE") or "dump").strip().lower()
    return raw if raw in ("dump", "scoped") else "dump"


def _clean_heading(heading: str) -> str:
    return MD_NOISE_RE.sub("", heading).strip()


def markdown_sections(text: str, *, max_level: int = 3) -> list[tuple[str, int, str]]:
    """Split markdown into (clean_heading, level, block_including_heading).

    Section end is the next heading with level <= current (standard markdown
    parent owns children). A matched ## registry entry therefore carries its
    ### Personality / Speech subsections.
    """
    if not text:
        return []
    matches = list(HEADING_LINE_RE.finditer(text))
    if not matches:
        return [(_clean_heading(text.splitlines()[0]), 1, text.strip())]
    sections: list[tuple[str, int, str]] = []
    if matches[0].start() > 0:
        pre = text[: matches[0].start()].strip()
        if pre:
            sections.append(("(preamble)", 0, pre))
    for i, m in enumerate(matches):
        level = len(m.group(1))
        if level > max_level:
            continue
        heading = _clean_heading(m.group(2))
        start = m.start()
        end = len(text)
        for nxt in matches[i + 1 :]:
            if len(nxt.group(1)) <= level:
                end = nxt.start()
                break
        block = text[start:end].strip()
        sections.append((heading, level, block))
    return sections


def _heading_name_candidates(heading: str) -> list[str]:
    """Pull likely character/place names out of a registry heading."""
    cleaned = MD_NOISE_RE.sub("", heading)
    cleaned = cleaned.replace('"', " ").replace("'", " ")
    cleaned = re.sub(r"^\W+", "", cleaned)
    # Drop parenthetical role labels
    cleaned = re.sub(r"\([^)]*\)", " ", cleaned)
    cleaned = SECTION_LABEL_RE.sub(lambda m: m.group(1) or "", cleaned).strip()
    names: list[str] = []
    for m in PROPER_NOUN_RE.finditer(cleaned):
        tok = m.group(1).strip()
        if len(tok) < 2:
            continue
        if tok.lower() in STOPWORDS or tok.upper() in ALLCAPS_NOISE:
            continue
        names.append(tok)
    # Prefer first+last Title Case sequence when present
    full = " ".join(names)
    if full and full.lower() not in STOPWORDS:
        names.append(full)
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def extract_query_terms(*texts: str, extra: list[str] | None = None, limit: int = 40) -> list[str]:
    """Deterministic proper-noun terms from outline/orientation/brief text."""
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        t = term.strip().strip(".,;:!?\"'()[]")
        if len(t) < 2 or t.lower() in STOPWORDS:
            return
        if t.upper() in ALLCAPS_NOISE and t.upper() == t:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append(t)

    for t in extra or []:
        add(t)
    for text in texts:
        if not text:
            continue
        for m in PROPER_NOUN_RE.finditer(text):
            add(m.group(1))
        # Orientation-style "Name: detail" lines
        for line in text.splitlines():
            if ":" in line:
                head = line.split(":", 1)[0].strip().lstrip("-* ")
                if head and head[0].isupper():
                    add(head)
    return terms[:limit]


def _term_matches_blob(term: str, blob: str) -> bool:
    if not term or not blob:
        return False
    return re.search(rf"\b{re.escape(term)}\b", blob, re.IGNORECASE) is not None


def _select_character_sections(
    characters_text: str, terms: list[str], *, max_sections: int | None = None
) -> tuple[list[tuple[str, str]], list[str]]:
    if max_sections is None:
        max_sections = MAX_CHARACTER_SECTIONS
    sections = markdown_sections(characters_text, max_level=3)
    scored: list[tuple[int, str, str]] = []
    for heading, level, block in sections:
        if level == 0 or heading == "(preamble)":
            continue
        candidates = _heading_name_candidates(heading)
        score = 0
        for term in terms:
            if any(_term_matches_blob(term, c) for c in candidates):
                score += 3
            elif _term_matches_blob(term, heading):
                score += 2
            elif _term_matches_blob(term, block[:800]):
                score += 1
        if re.match(r"^(?:\*\*)?(?:1\.|I\.)", heading) or "narrator" in heading.lower():
            score += 2
        if score > 0:
            scored.append((score, heading, block))
    scored.sort(key=lambda x: -x[0])
    picked = [(h, b) for _, h, b in scored[:max_sections]]
    hits = [h for h, _ in picked]
    return picked, hits


def _select_world_sections(
    world_text: str, terms: list[str], *, max_sections: int | None = None
) -> tuple[list[tuple[str, str]], list[str]]:
    """Select world sections. Parents (##) carry their ### children via
    markdown_sections parent-owned span; level<=2 candidates only so a lone
    ### is not preferred over its ## parent when the parent already matched.
    """
    if max_sections is None:
        max_sections = MAX_WORLD_SECTIONS
    sections = markdown_sections(world_text, max_level=3)
    scored: list[tuple[int, str, str]] = []
    for heading, level, block in sections:
        if heading == "(preamble)":
            continue
        if level > 2:
            continue
        score = 0
        for term in terms:
            if _term_matches_blob(term, heading):
                score += 3
            elif _term_matches_blob(term, block[:1000]):
                score += 1
        if level <= 2 and score > 0:
            score += 1
        if score > 0:
            scored.append((score, heading, block))
    scored.sort(key=lambda x: -x[0])
    picked = [(h, b) for _, h, b in scored[:max_sections]]
    hits = [h for h, _ in picked]
    return picked, hits


def _join_sections(picked: list[tuple[str, str]]) -> str:
    return "\n\n".join(block for _, block in picked).strip()


# Outline/orientation schema labels, not story entities. Multi-word labels only,
# so a character legitimately named "Try" or "High" is not dropped from query
# terms (the old single-token denylist did exactly that).
SCHEMA_LABELS = frozenset(
    p.strip().lower() for p in """scene stakes|emotional arc|orientation facts|
    chapter question|try-fail cycle|high reflection|location|summary|plants|
    scene|stakes|arc""".split("|") if p.strip()
)


def _is_schema_label(term: str) -> bool:
    return term.strip().lower() in SCHEMA_LABELS


def build_retrieval_pack(
    *,
    chapter_num: int,
    chapter_outline: str = "",
    characters_text: str = "",
    world_text: str = "",
    canon_view: str = "",
    extra_texts: list[str] | None = None,
    orientation_facts: list[str] | None = None,
    mode: str | None = None,
) -> RetrievalPack:
    """Build the context pack for one chapter. dump = full texts; scoped = hits."""
    mode = mode or retrieval_mode()
    terms = extract_query_terms(
        chapter_outline,
        *(extra_texts or []),
        extra=list(orientation_facts or []),
    )
    # Drop schema/outline chrome that is not a story entity. Multi-word labels
    # only; a bare capitalized word is left alone so real names survive.
    terms = [t for t in terms if not _is_schema_label(t)]

    if mode != "scoped":
        return RetrievalPack(
            mode="dump",
            characters_block=characters_text.strip(),
            world_block=world_text.strip(),
            canon_view=canon_view,
            query_terms=terms,
            character_hits=[],
            world_hits=[],
            character_fallback=False,
            world_fallback=False,
        )

    char_picked, char_hits = _select_character_sections(characters_text, terms)
    world_picked, world_hits = _select_world_sections(world_text, terms)

    character_fallback = False
    world_fallback = False
    characters_block = _join_sections(char_picked)
    world_block = _join_sections(world_picked)

    # Under-retrieval guard: never starve the writer of an entire bible.
    if characters_text.strip() and not characters_block:
        characters_block = characters_text.strip()
        character_fallback = True
        char_hits = ["(fallback: full registry)"]
    if world_text.strip() and not world_block:
        world_block = world_text.strip()
        world_fallback = True
        world_hits = ["(fallback: full world bible)"]

    # Honest telemetry: a "hit" that is ~the whole file is dump, not scope.
    if (
        not character_fallback
        and characters_text.strip()
        and len(characters_block) >= FULLTEXT_HIT_RATIO * len(characters_text.strip())
    ):
        character_fallback = True
        char_hits = list(char_hits) + ["(fallback: scoped hit ≈ full registry)"]
    if (
        not world_fallback
        and world_text.strip()
        and len(world_block) >= FULLTEXT_HIT_RATIO * len(world_text.strip())
    ):
        world_fallback = True
        world_hits = list(world_hits) + ["(fallback: scoped hit ≈ full world bible)"]

    return RetrievalPack(
        mode="scoped",
        characters_block=characters_block,
        world_block=world_block,
        canon_view=canon_view,
        query_terms=terms,
        character_hits=char_hits,
        world_hits=world_hits,
        character_fallback=character_fallback,
        world_fallback=world_fallback,
    )


def write_retrieval_telemetry(pack: RetrievalPack, chapter_num: int, path) -> None:
    """Write retrieval telemetry; fail-soft with a stderr warning (not silent)."""
    try:
        from core import paths as paths_mod
        data = pack.to_telemetry()
        data["chapter"] = chapter_num
        paths_mod.save_json_atomic(data, path)
        paths_mod.retire_shadowing_sidecar(path)
    except Exception as e:
        import sys
        print(f"WARN: retrieval telemetry failed for ch{chapter_num}: {e}", file=sys.stderr)
