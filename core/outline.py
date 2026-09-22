"""Outline text operations: headings, premise beats, plants/harvests."""

import re
import sys

from core.paths import get_outline_path


def normalize_chapter_heading(text: str, chapter_num: int) -> str:
    """Normalize a chapter file's first line to '# Chapter N: <title>'.

    Draft/revision LLMs sometimes emit the title as '**Chapter N: Title**',
    '# **Title**', or with trailing emphasis residue. Unify the header format
    so build_tex.py, build_outline.py, and the manuscript always see a
    consistent title line. Leaves the text untouched when the first line is
    prose, not a title.

    The title itself is overridden by the foundation outline's title for the
    chapter (when one exists) — the outline is the single source of truth for
    chapter titles, so drafter/revision LLM title drift (slug codenames,
    'The X' inflation) is mechanically corrected at write time.
    """
    stripped = text.lstrip('\n')
    lines = stripped.split('\n', 1)
    first = lines[0].strip()
    rest = lines[1] if len(lines) > 1 else ''

    m = re.match(r'^#{1,6}\s*(.+?)\s*$', first)
    if not m:
        m = re.match(r'^\*\*(.+?)\*\*\s*$', first)
    if not m:
        return text
    title = m.group(1).strip().strip('*').strip()
    # Drop a leading "Chapter N" label only when it's separated from the real
    # title (": ", em/en dash) or is the entire title ("Chapter 20").
    title = re.sub(
        r'^Chapter\s+\d+\s*[:—–-]\s*', '', title, flags=re.IGNORECASE
    ).strip()
    if re.fullmatch(r'Chapter\s+\d+', title, flags=re.IGNORECASE):
        title = ''

    foundation_title = _foundation_chapter_title(chapter_num)
    if foundation_title:
        title = foundation_title

    heading = f'# Chapter {chapter_num}: {title}' if title else f'# Chapter {chapter_num}'
    return '\n'.join([heading, rest]).rstrip() + '\n'

def _foundation_chapter_title(chapter_num: int) -> str:
    """Return the foundation outline's title for a chapter, or ''.

    Scoped to the DETAILED section so a HIGH-LEVEL ROADMAP one-liner is never
    picked instead of the real entry. Handles both '### Chapter N:' (fresh
    outlines) and '### Ch N:' (rebuilt post-export outlines).
    """
    outline_path = get_outline_path()
    if not outline_path.exists():
        return ''
    try:
        outline_text = outline_path.read_text(encoding='utf-8')
    except Exception:
        return ''
    if '## DETAILED CHAPTER OUTLINES' in outline_text:
        outline_text = outline_text.split('## DETAILED CHAPTER OUTLINES', 1)[1]
    pattern = rf'^###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{chapter_num}\b.*?[:—–][ \t]*(.+?)[ \t]*$'
    m = re.search(pattern, outline_text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return ''
    title = m.group(1).strip().strip('*').strip()
    # Reject snake_case codenames and empty labels so a sanitize failure can't
    # propagate slug titles into the manuscript.
    if re.search(r'[a-z]_[a-z]', title):
        return ''
    return title

def validate_generator_output(content: str, name: str, min_len: int = 100, expected_headers: list[str] | None = None) -> str:
    """Guardrail: check a foundation generator's output is non-empty, meets min length,
    and has expected headers. Returns the stripped content on success.
    Raises RuntimeError on failure."""
    content = content.strip()
    if not content:
        raise RuntimeError(f"{name}: output is empty")
    if len(content) < min_len:
        raise RuntimeError(f"{name}: output too short ({len(content)} chars, minimum {min_len})")
    if expected_headers:
        for h in expected_headers:
            if h not in content:
                raise RuntimeError(f"{name}: output missing expected header '{h}'")
    return content

def extract_chapter_outline(outline_text: str, chapter_num: int) -> str:
    """Extract a specific chapter's outline entry from the DETAILED section.

    Scoped to '## DETAILED CHAPTER OUTLINES' so the HIGH-LEVEL ROADMAP one-liner
    is never matched instead of the real beats entry. Raises ValueError if missing.
    """
    if "## DETAILED CHAPTER OUTLINES" in outline_text:
        outline_text = outline_text.split("## DETAILED CHAPTER OUTLINES", 1)[1]
    pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{chapter_num}\b.*?(?=###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*(?:\d+)\b|## Act|## Foreshadowing|$)'
    match = re.search(pattern, outline_text, re.IGNORECASE | re.DOTALL)
    if not match:
        raise ValueError(
            f"Chapter {chapter_num} outline entry not found in the "
            f"## DETAILED CHAPTER OUTLINES section — refusing to draft without beats."
        )
    return match.group(0).strip()

def extract_next_chapter_outline(outline_text: str, chapter_num: int) -> str:
    """Extract the next chapter's outline (first few lines for continuity)."""
    try:
        next_entry = extract_chapter_outline(outline_text, chapter_num + 1)
    except ValueError:
        return "(final chapter)"
    lines = next_entry.split('\n')[:10]
    return '\n'.join(lines)

def _normalize_beat_label(label: str) -> str:
    """Normalize a beat label for fuzzy matching — remove bold, POV, numbering."""
    label = re.sub(r'\*\*', '', label)
    label = re.sub(r'\([^)]*\)', '', label)
    label = re.sub(r'^\d+[\.\)]\s*', '', label)
    label = label.replace('_', ' ').replace('/', ' ').replace('-', ' ')
    label = re.sub(r'\s+', ' ', label).strip()
    return label.lower()

def _beats_match(required: str, present: str) -> bool:
    """Token-set containment check: all required words appear in present label."""
    r_tokens = set(_normalize_beat_label(required).split())
    p_tokens = set(_normalize_beat_label(present).split())
    return r_tokens.issubset(p_tokens) and len(r_tokens) > 0

def _parse_bold_numbered_beats(outline_text: str) -> list[dict]:
    """Fallback — extract beats from bold-numbered header format.

    Handles:
      **1. beat_label (POV info)**
      Paragraph text accumulates as scene_summary until next beat header.
      *1. beat_label* (single-asterisk variant)
    """
    lines = outline_text.split('\n')
    in_section = False
    beats = []
    current_beat = None
    current_summary: list[str] = []

    for line in lines:
        stripped = line.strip()

        header_match = re.match(
            r'^#{0,3}\s*\**\s*PREMISE\s+BEATS\**\s*:?\**\s*$', stripped, re.IGNORECASE
        )
        if header_match:
            in_section = True
            continue

        if not in_section:
            continue

        end_match = re.match(
            r'^(?:#{1,3}\s|MAIN\s+PLOT)', stripped, re.IGNORECASE
        )
        if end_match:
            if current_beat is not None:
                beats.append({"beat": current_beat, "scene_summary": ' '.join(current_summary).strip()})
            break

        beat_header_match = re.match(
            r'^\s*\*+\s*\d+[\.\)]\s+(.+?)\s*\*+\s*$', stripped
        )
        if beat_header_match:
            if current_beat is not None:
                beats.append({"beat": current_beat, "scene_summary": ' '.join(current_summary).strip()})
            current_beat = beat_header_match.group(1).strip()
            current_summary = []
            continue

        if current_beat is not None and stripped:
            current_summary.append(stripped)

    if in_section and current_beat is not None:
        beats.append({"beat": current_beat, "scene_summary": ' '.join(current_summary).strip()})

    return beats

def _parse_plain_numbered_beats(outline_text: str) -> list[dict]:
    """Fallback — extract beats from plain numbered format:

      1. beat_label: scene summary
      2. beat_label: scene summary

    Matches the style most models naturally produce when told a 'numbered list'.
    """
    lines = outline_text.split('\n')
    in_section = False
    beats = []

    for line in lines:
        stripped = line.strip()

        header_match = re.match(
            r'^#{0,3}\s*\**\s*PREMISE\s+BEATS\**\s*:?\**\s*$', stripped, re.IGNORECASE
        )
        if header_match:
            in_section = True
            continue

        end_match = re.match(
            r'^(?:#{1,3}\s|MAIN\s+PLOT)', stripped, re.IGNORECASE
        )
        if in_section and end_match:
            break

        if in_section:
            numbered_match = re.match(r'^\s*\d+[\.\)]\s+(.+)$', stripped)
            if not numbered_match:
                continue
            content = numbered_match.group(1).strip()
            colon_idx = content.find(':')
            if colon_idx == -1:
                beats.append({"beat": content.strip(), "scene_summary": ""})
            else:
                beat_label = content[:colon_idx].strip()
                scene_summary = content[colon_idx + 1:].strip()
                beats.append({"beat": beat_label, "scene_summary": scene_summary})

    return beats

def parse_premise_beats(outline_text: str) -> list[dict]:
    """
    Extract premise beats from Chapter 1's PREMISE BEATS section in the outline.
    Returns list of {"beat": str, "scene_summary": str} in order found.
    Returns empty list if no PREMISE BEATS section is found.

    Tries three formats in order:
      1. Bullet lines:  - beat_label: scene summary
      2. Plain numbered: N. beat_label: scene summary
      3. Bold-numbered: **N. beat_label (POV info)** then paragraph
    """
    lines = outline_text.split('\n')
    in_section = False
    beats = []

    for line in lines:
        stripped = line.strip()

        header_match = re.match(
            r'^#{0,3}\s*\**\s*PREMISE\s+BEATS\**\s*:?\**\s*$', stripped, re.IGNORECASE
        )
        if header_match:
            in_section = True
            continue

        end_match = re.match(
            r'^(?:#{1,3}\s|MAIN\s+PLOT)', stripped, re.IGNORECASE
        )
        if in_section and end_match:
            break

        if in_section:
            bullet_match = re.match(r'^[-*+]\s+(.+)$', stripped)
            if not bullet_match:
                continue
            content = bullet_match.group(1).strip()
            colon_idx = content.find(':')
            if colon_idx == -1:
                beats.append({"beat": content.strip(), "scene_summary": ""})
            else:
                beat_label = content[:colon_idx].strip()
                scene_summary = content[colon_idx + 1:].strip()
                beats.append({"beat": beat_label, "scene_summary": scene_summary})

    if not beats:
        beats = _parse_plain_numbered_beats(outline_text)
    if not beats:
        beats = _parse_bold_numbered_beats(outline_text)

    return beats

def validate_premise_beats(required_beats: list[str], outline_text: str) -> tuple[bool, str]:
    """
    Validate that the required premise beats appear in the outline.

    Primary path: Chapter 1's PREMISE BEATS section contains all required
    beats in relative order (subsequence match, not exact match — extra
    unlisted beats between required ones are allowed).  Uses token-set
    matching so human-readable labels like "Ordinary World / Otaku Life"
    match slug keys like "ordinary_world_otaku_life".

    Fallback path: if Chapter 1 has no PREMISE BEATS section, accept beats
    distributed across the whole outline (the roadmap writer is told to
    spread premise beats across early chapters), checking every chapter's
    scene-beat/summary text for each required beat.

    Returns (passed: bool, error_message: str).
    """
    ch1 = _chapter_entry(outline_text, 1)
    present_labels = [b["beat"] for b in parse_premise_beats(ch1)] if ch1 else []

    if present_labels:
        missing = _find_missing_beats(required_beats, present_labels)
        if not missing:
            return True, ""
        return False, f"Missing premise beat(s): {', '.join(missing)}"

    # No PREMISE BEATS section in Chapter 1 — scan the whole outline for
    # each required beat's tokens appearing in any chapter's text.
    missing = [b for b in required_beats if not _beat_tokens_in_text(b, outline_text)]
    if not missing:
        return True, ""
    return False, f"Missing premise beat(s): {', '.join(missing)}"

def _chapter_entry(outline_text: str, chapter_num: int) -> str:
    """Return the detailed outline entry text for a chapter, or '' if absent."""
    idx = re.search(
        rf'^###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{chapter_num}\b',
        outline_text, re.IGNORECASE | re.MULTILINE)
    if not idx:
        return ""
    start = idx.start()
    nxt = re.search(
        r'^###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*\d+\b',
        outline_text[start + 1:], re.IGNORECASE | re.MULTILINE)
    end = start + 1 + nxt.start() if nxt else len(outline_text)
    return outline_text[start:end]

def _find_missing_beats(required_beats: list[str], present_labels: list[str]) -> list[str]:
    """Subsequence-match required beats against present labels, return missing."""
    missing = []
    it = iter(present_labels)
    for required in required_beats:
        found = False
        for p in it:
            if _beats_match(required, p):
                found = True
                break
        if not found:
            missing.append(required)
    return missing

def _beat_tokens_in_text(beat_label: str, text: str) -> bool:
    """Check that every normalized token of a beat label is present in text.

    Matches exact words first, then fuzzy word matches (e.g. 'rebirth' vs
    'reborn') via difflib, so concept drift in the outline still validates.
    """
    import difflib
    tokens = _normalize_beat_label(beat_label).split()
    if not tokens:
        return False
    lowered = text.lower()
    words = set(lowered.split())
    for t in tokens:
        if t in lowered:
            continue
        if difflib.get_close_matches(t, words, n=1, cutoff=0.6):
            continue
        return False
    return True

_PLANT_TAG_RE = re.compile(
    # The description runs to the closing bracket and may contain apostrophes,
    # commas and dashes. It used to be `[^'"\]]+`, which silently dropped every
    # tag whose description contained a possessive — "Baal II's soul",
    # "Lily's hands" — hiding 23 of v4's 42 plant tags.
    # Slugs accept apostrophes (curly and straight) and dots: real outlines use
    # them ("the_king's_migraine", "slug.with.dots"), and a narrower class made
    # the leak scanner blind to exactly those tags.
    r"""\[(Plant|Harvest)\s*:\s*([^\s:\[\]]+)\s*[-:]\s*([^\[\]]+?)\s*\]""",
    re.IGNORECASE,
)


_QUOTE_PAIRS = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019"}


def _unquote(text: str) -> str:
    """Remove one matched wrapping quote pair, if present.

    Only a *pair* is removed. Stripping a quote character set would eat a
    legitimate trailing apostrophe ("the generals'" -> "the generals").
    """
    d = text.strip()
    for opener, closer in _QUOTE_PAIRS.items():
        if len(d) >= 2 and d.startswith(opener) and d.endswith(closer):
            return d[1:-1].strip()
    return d


def chapter_sections(outline_text: str) -> dict:
    """Split an outline into {chapter number: section text}.

    Only text after the first chapter heading is returned. (Keeping the preamble
    under a chapter 0 was tried and reverted: it recovered nothing for the real
    project, and the HIGH-LEVEL ROADMAP reuses the same `### Chapter N` headings,
    so a roadmap entry would be overwritten by its DETAILED namesake. The tags
    that were invisible were lost to the quote-hostile description regex, not to
    this split — see `_PLANT_TAG_RE`.)
    """
    sections = {}
    current_ch = None
    current_lines = []
    for line in outline_text.splitlines():
        cleaned = line.strip().replace('*', '').replace('_', '')
        m = re.match(r'^###\s*(?:Chapter|Ch\.?)\s*(\d+)\b', cleaned, re.IGNORECASE)
        if m:
            if current_ch is not None:
                sections[current_ch] = "\n".join(current_lines)
            current_ch = int(m.group(1))
            current_lines = []
        if current_ch is not None:
            current_lines.append(line)
    if current_ch is not None:
        sections[current_ch] = "\n".join(current_lines)
    return sections


def scan_plant_tag_marks(text: str) -> list[str]:
    """Verbatim `[Plant: …]` / `[Harvest: …]` marks in arbitrary text.

    For leak checks (prose must never contain planning tags). Uses the same
    `_PLANT_TAG_RE` owner as the parser, so a scanner cannot drift from the
    format it is looking for.
    """
    return [m.group(0) for m in _PLANT_TAG_RE.finditer(text or "")]


def parse_plant_tags(outline_text: str) -> tuple[list[dict], list[dict]]:
    """Every `[Plant: slug - "desc"]` / `[Harvest: …]` tag, as (plants, harvests).

    One owner for the tag format. This used to be three regexes that disagreed:
    the validator's, a copy inside `extract_outline_debts`, and a stricter one in
    `gen_outline` that required quotes and forbade hyphens in slugs — so whether
    a tag existed depended on which caller you asked.

    Each entry is {"chapter": int, "slug": str, "desc": str}, both lowercased.
    """
    plants, harvests = [], []
    for chapter, content in chapter_sections(outline_text).items():
        for kind, slug, desc in _PLANT_TAG_RE.findall(content):
            entry = {
                "chapter": chapter,
                "slug": slug.strip().lower(),
                # Descriptions are usually quoted; only a matched pair is removed.
                "desc": _unquote(desc).lower(),
            }
            (plants if kind.lower() == "plant" else harvests).append(entry)
    return plants, harvests


def validate_plants_harvests(outline_text: str) -> tuple[bool, str]:
    """
    Validate that all plants and harvests in outline.md are logically consistent:
    - Every harvest must have a corresponding plant in an earlier chapter.
    - Matches are resolved using slugs. If a slug doesn't match exactly, 
      token-set overlap fallback is used.
    """
    import re

    plants, harvests = parse_plant_tags(outline_text)

    errors = []
    
    # Check each harvest
    for h in harvests:
        matched_plant = None
        # Try exact slug match
        for p in plants:
            if p["slug"] == h["slug"]:
                matched_plant = p
                break
                
        # If no exact slug match, try fuzzy matching via token-set overlap on description
        if not matched_plant:
            h_words = set(w for w in h["desc"].split() if len(w) >= 4)
            best_overlap = 0
            best_p = None
            for p in plants:
                p_words = set(w for w in p["desc"].split() if len(w) >= 4)
                overlap = len(h_words.intersection(p_words))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_p = p
            if best_overlap >= 3: # Require at least 3 matching words of length >= 4
                matched_plant = best_p
                print(f"[INFO] Fuzzy matched harvest slug '{h['slug']}' with plant slug '{best_p['slug']}' via description token overlap.", file=sys.stderr)

        if not matched_plant:
            errors.append(f"Dangling harvest: '[Harvest: {h['slug']} - \"{h['desc']}\"]' in Chapter {h['chapter']} has no corresponding plant setup in previous chapters.")
        elif matched_plant["chapter"] >= h["chapter"]:
            errors.append(f"Order error: '[Harvest: {h['slug']}]' in Chapter {h['chapter']} occurs before or in the same chapter as its plant setup '[Plant: {matched_plant['slug']}]' in Chapter {matched_plant['chapter']}.")

    if errors:
        return False, "\n".join(errors)
    return True, ""

_DEBT_RE = re.compile(r'^Ch\s*(\d+)\s*Setup:\s*([A-Za-z0-9_\-]+)\s*-\s*"(.*)"\s*$')


def parse_debt(entry: str) -> dict | None:
    """Debts are stored as `Ch 3 Setup: slug - "desc"` strings."""
    m = _DEBT_RE.match((entry or "").strip())
    if not m:
        return None
    return {"chapter": int(m.group(1)), "slug": m.group(2).lower(),
            "desc": m.group(3).strip()}


def open_debts_for_chapter(debts, chapter: int, limit: int = 3) -> list:
    """Unpaid setups declared before `chapter`, oldest first.

    A debt is by construction a plant that appears in no harvest. The consumer
    used to match a chapter's *harvest* slugs against these debt strings, which
    can never be equal — so the "narrative debts" guardrail had never once
    fired, and nothing in the pipeline could cause an unpaid plant to be paid
    off. Surfacing them here is what makes that possible.
    """
    out = []
    for entry in debts or []:
        parsed = parse_debt(entry)
        if parsed and parsed["chapter"] < chapter:
            out.append(parsed)
    out.sort(key=lambda d: (d["chapter"], d["slug"]))
    return out[:limit]


def extract_outline_debts(outline_text: str) -> list[str]:
    """Plant slugs that have no harvest anywhere in the outline."""
    plants, harvests = parse_plant_tags(outline_text)
    harvested_slugs = {h["slug"] for h in harvests}
    debts = []
    for p in plants:
        if p["slug"] not in harvested_slugs:
            debts.append(f"Ch {p['chapter']} Setup: {p['slug']} - \"{p['desc']}\"")
            
    return debts
