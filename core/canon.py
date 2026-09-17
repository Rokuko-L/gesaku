"""Canon.md parsing and chapter-scoped writer/judge views.

Splits canon.md into Foundation / Core / As-of / Revision Sync, then builds
knowledge-gated views for drafting and scoring. Sealed foundation facts
(`visible_from > 1`) are excluded from writer and judge prompts until the
reveal chapter; they remain available to outline hygiene and retrofit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

VISIBLE_FROM_RE = re.compile(
    r"^\s*[-*]\s*(?:"
    r"\[(?:visible_from|vf|from)\s*[:= ]\s*(\d+)\s*\]|"
    r"\(from\s*[:= ]\s*(\d+)\s*\)|"
    r"(?:visible_from|vf)\s*[:= ]\s*(\d+)\s*[:.)-]?\s*|"
    r"from\s*[:=]\s*(\d+)\s*[:.)-]?\s*"
    r")\s*(.+?)\s*$",
    re.IGNORECASE,
)
BARE_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
# Any bullet that *looks* like it attempted a reveal tag. Used to fail closed:
# a typo must never silently become visible_from=1. Deliberately tight so
# prose like "From the capital..." is not misread as a tag.
TAG_ATTEMPT_RE = re.compile(
    r"^\s*[-*]\s*(?:"
    r"\[\s*(?:visible_from|vf|from)\b|"
    r"\(\s*from\b|"
    r"(?:visible_from|vf)\b|"
    r"from\s*[:=]"
    r")",
    re.IGNORECASE,
)
# Max seal for malformed tags — never public, unlock only if a chapter this
# large is actually reached (effectively author-only until fixed).
MALFORMED_SEAL = 10**6
MEANING_FRAMES = re.compile(
    r"\b(secretly|actually|really|truly|in truth|the real|mask|front for|"
    r"puppet master|puppet-master|is (?:in fact|in reality)|was never|"
    r"hidden (?:identity|agenda|truth)|true (?:identity|nature|role|protagonist)|"
    r"red herring|decoy)\b",
    re.IGNORECASE,
)
STOPWORDS = frozenset(
    """a an and are as at be but by for from has have if in into is it its of on or
    she he they them their this that the then there these those to was were will
    with without not no yes her his him who whom which when where why how all any
    both each few more most other some such only own same so than too very can
    just about after before under over again further once here why what while
    does did doing done would could should must may might shall""".split()
)


@dataclass
class FoundationFact:
    fact: str
    visible_from: int = 1
    malformed_tag: bool = False


@dataclass
class ParsedCanon:
    foundation_facts: list[FoundationFact] = field(default_factory=list)
    foundation_raw: str = ""
    core_facts: list[str] = field(default_factory=list)
    core_raw: str = ""
    as_of: dict[int, list[str]] = field(default_factory=dict)
    as_of_raw: dict[int, str] = field(default_factory=dict)
    revision_sync: str = ""
    other_sections: list[str] = field(default_factory=list)
    malformed_visible_from: list[str] = field(default_factory=list)

    def reveal_chapter(self) -> int | None:
        """Earliest chapter at which any sealed fact becomes visible, else None.

        Malformed-tag facts (sealed to MALFORMED_SEAL) are ignored here so a
        typo does not invent a fake reveal at chapter 10**6.
        """
        sealed = [
            f.visible_from
            for f in self.foundation_facts
            if f.visible_from > 1 and not f.malformed_tag
        ]
        return min(sealed) if sealed else None

    def sealed_facts(self) -> list[FoundationFact]:
        return [f for f in self.foundation_facts if f.visible_from > 1]

    def public_facts(self) -> list[FoundationFact]:
        return [f for f in self.foundation_facts if f.visible_from <= 1]


def _split_sections(canon_text: str) -> list[tuple[str, str]]:
    """Return [(header, body_including_header), ...] for `## ` sections."""
    sections: list[tuple[str, str]] = []
    current_header = ""
    current: list[str] = []
    for line in canon_text.splitlines(keepends=True):
        if line.startswith("## "):
            if current_header:
                sections.append((current_header, "".join(current)))
            current_header = line.strip()
            current = [line]
        elif current_header:
            current.append(line)
    if current_header:
        sections.append((current_header, "".join(current)))
    return sections


def _parse_bullets(body: str) -> list[str]:
    facts = []
    for line in body.splitlines():
        m = BARE_BULLET_RE.match(line)
        if m:
            facts.append(m.group(1).strip())
    return facts


def _parse_foundation(body: str) -> tuple[list[FoundationFact], list[str]]:
    """Parse foundation body into facts with optional visible_from tags.

    Accepts:
      - visible_from=14: fact
      - visible_from 14: fact   (space separator)
      - [visible_from: 14] fact
      - (from 14) fact
      - plain bullet  → visible_from=1

    Fail closed: a bullet that *looks* like a reveal tag but does not parse
    is sealed to MALFORMED_SEAL (never public). Malformed lines are returned
    for logging/gen_canon retry.
    """
    facts: list[FoundationFact] = []
    malformed: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            continue
        vm = VISIBLE_FROM_RE.match(line)
        if vm:
            raw = next(g for g in vm.groups()[:4] if g)
            text = vm.group(5).strip().lstrip(":-–— ").strip()
            if text:
                facts.append(FoundationFact(fact=text, visible_from=int(raw)))
            continue
        bm = BARE_BULLET_RE.match(line)
        if not bm:
            continue
        text = bm.group(1).strip()
        if not text or text.startswith("#"):
            continue
        if TAG_ATTEMPT_RE.match(line):
            # Typo / unparseable tag — seal hard, do not open to chapter 1.
            stripped = re.sub(
                r"^[\[\(]?\s*(?:visible_from|vf|from)\b[:= ]*\d*\s*[\]\):.\-]?\s*",
                "",
                text,
                flags=re.IGNORECASE,
            ).strip() or text
            facts.append(
                FoundationFact(
                    fact=stripped,
                    visible_from=MALFORMED_SEAL,
                    malformed_tag=True,
                )
            )
            malformed.append(line.strip())
            continue
        facts.append(FoundationFact(fact=text, visible_from=1))
    return facts, malformed


def parse_canon(canon_text: str) -> ParsedCanon:
    """Split canon.md into structured sections."""
    parsed = ParsedCanon()
    if not canon_text or not canon_text.strip():
        return parsed

    for header, body in _split_sections(canon_text):
        h = header.lower()
        if h.startswith("## foundation"):
            parsed.foundation_raw = body
            parsed.foundation_facts, parsed.malformed_visible_from = _parse_foundation(body)
        elif h.startswith("## core canon"):
            parsed.core_raw = body
            parsed.core_facts = _parse_bullets(body)
        elif h.startswith("## as of chapter"):
            m = re.search(r"##\s+As of Chapter\s+(\d+)", header, re.IGNORECASE)
            if not m:
                continue
            n = int(m.group(1))
            parsed.as_of_raw[n] = body
            parsed.as_of[n] = _parse_bullets(body)
        elif h.startswith("## revision sync"):
            parsed.revision_sync = body
        else:
            parsed.other_sections.append(body)

    return parsed


def public_foundation_md(parsed: ParsedCanon, chapter_num: int) -> str:
    """Foundation facts visible to the writer at chapter_num."""
    lines = [
        f"- {f.fact}"
        for f in parsed.foundation_facts
        if f.visible_from <= chapter_num
    ]
    if lines:
        return "## Foundation\n\n" + "\n".join(lines) + "\n"
    return ""


def sealed_foundation_md(parsed: ParsedCanon) -> str:
    """All currently sealed facts (for hygiene/retrofit, never drafting)."""
    sealed = parsed.sealed_facts()
    if not sealed:
        return ""
    lines = [f"- visible_from={f.visible_from}: {f.fact}" for f in sealed]
    return "## Foundation (Sealed)\n\n" + "\n".join(lines) + "\n"


def core_md(parsed: ParsedCanon) -> str:
    if not parsed.core_facts:
        return ""
    return "## Core Canon\n\n" + "\n".join(f"- {f}" for f in parsed.core_facts) + "\n"


def disclosure_md(parsed: ParsedCanon, chapter_num: int) -> str:
    """As-of sections with chapter index strictly before chapter_num."""
    parts = []
    for n in sorted(parsed.as_of_raw):
        if n < chapter_num:
            parts.append(parsed.as_of_raw[n].rstrip() + "\n")
    return "\n".join(parts).strip() + "\n" if parts else ""


def writer_view_md(parsed: ParsedCanon, chapter_num: int) -> str:
    """Canon block for drafting/revision prompts (no sealed facts, no future as-of)."""
    blocks = [
        public_foundation_md(parsed, chapter_num),
        core_md(parsed),
        disclosure_md(parsed, chapter_num),
    ]
    return "\n".join(b for b in blocks if b).strip()


def judge_view_md(parsed: ParsedCanon, chapter_num: int) -> str:
    """Canon block for the chapter judge — same knowledge as the reader."""
    return writer_view_md(parsed, chapter_num)


def sealed_denylist_terms(parsed: ParsedCanon, min_len: int = 4) -> list[str]:
    """Content terms from sealed facts that must not appear in pre-reveal outline."""
    terms: set[str] = set()
    for f in parsed.sealed_facts():
        for word in re.findall(r"[A-Za-z][A-Za-z'-]{%d,}" % (min_len - 1), f.fact):
            low = word.lower().strip("'-")
            if low not in STOPWORDS and low not in {"fact", "chapter", "visible", "from"}:
                terms.add(low)
        # Meaning frames are always banned in pre-reveal beats
        for m in MEANING_FRAMES.finditer(f.fact):
            terms.add(m.group(0).lower())
    # Always include the generic meaning frames regardless of sealed content
    for frame in (
        "secretly", "actually", "the real", "true identity", "true nature",
        "in truth", "hidden agenda", "front for", "puppet",
    ):
        terms.add(frame)
    return sorted(terms)


def _used_as_term(facts: list[str], name: str) -> bool:
    """True when every mention is article-led or enumerated: "the Law", "Law III".

    That pattern means canon *terminology* — a force, a rule, a mechanism —
    rather than a character. v4's sealed facts say "Law III - Resonance Bleed
    is irreversible" and characters.md lists "**The Law**" among the forces
    acting on Corvo, which is how a noun ends up in the required-character list
    while appearing in none of the pre-reveal chapters.

    Matching is case-sensitive on purpose: the common noun in "kingdom law
    classifies her" is not a mention of the term "Law", and counting it would
    keep the term in the list.
    """
    mentions = 0
    for fact in facts:
        for m in re.finditer(rf"\b{re.escape(name)}\b", fact):
            mentions += 1
            before = fact[:m.start()].rstrip()
            after = fact[m.end():].lstrip()
            # A real word boundary, not endswith("the"): "hefts the scythe"
            # ends with those three letters without being an article.
            article_led = bool(re.search(r"(?:^|[^A-Za-z])the$", before.lower()))
            enumerated = bool(re.match(r"^(?:[IVXL]+|\d+)\b", after))
            if not (article_led or enumerated):
                return False
    return mentions > 0


def action_plant_characters(parsed: ParsedCanon, characters_text: str = "") -> list[str]:
    """Proper names from sealed facts that also appear in the character registry.

    These characters should have action-shaped pre-reveal plants so a post-reveal
    retrofit has concrete material to work with.

    The registry test is word-bounded. It used to be `name in characters_text`,
    a substring test against the whole document, which admitted every short
    token ("I", "Arc") because the letters occur somewhere in the file — that is
    what made the hygiene gate fail on names that were never characters.
    """
    if not characters_text:
        return []
    facts = [f.fact for f in parsed.sealed_facts()]
    sealed_blob = " ".join(facts)
    name_hits: set[str] = set()
    for m in re.finditer(r"\b([A-Z][a-z]*(?:\s+[A-Z][a-z]+)*)\b", sealed_blob):
        name = m.group(1).strip()
        if not name:
            continue
        # Single-letter names (A, B) are legitimate in short fiction, so they
        # stay. Two exceptions: the narrator's "I" is a pronoun, never a
        # character, and "T-1"/"T-5" is a power tier written as a letter plus a
        # numeral.
        if name == "I":
            continue
        if re.match(r"\s*-\s*\d", sealed_blob[m.end():]):
            continue
        if len(name) > 1 and name.lower() in STOPWORDS:
            continue
        if not re.search(rf"\b{re.escape(name)}\b", characters_text):
            continue
        if _used_as_term(facts, name):
            continue
        name_hits.add(name)
    return sorted(name_hits)
