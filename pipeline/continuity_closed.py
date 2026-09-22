"""Closed-pass continuity: deterministic Findings A. No LLM.

CLI: uv run python pipeline/continuity_closed.py <chapter_num>

Writes eval_logs/continuity_chNN_closed.json. Quality findings warn;
severity:structural is reserved/unused by gates today.
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import re

from core import canon as canon_mod
from core import continuity_text
from core import outline as outline_mod
from core import paths


def _load(path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def run_closed_pass(chapter_num: int) -> dict:
    chapters_dir = paths.get_chapters_dir()
    ch_path = chapters_dir / f"ch_{chapter_num:02d}.md"
    chapter_text = _load(ch_path)

    characters_text = _load(paths.get_characters_path())
    world_text = _load(paths.get_world_path())
    canon_text = _load(paths.get_canon_path())

    prior_parts = []
    for n in range(1, chapter_num):
        prior_parts.append(_load(chapters_dir / f"ch_{n:02d}.md"))
    prior_text = "\n".join(prior_parts)

    parsed = canon_mod.parse_canon(canon_text) if canon_text else None
    if parsed is not None:
        canon_view = canon_mod.writer_view_md(parsed, chapter_num)
        sealed_terms = canon_mod.sealed_denylist_terms(parsed)
        reveal = parsed.reveal_chapter()
        sealed_facts = [f.fact for f in parsed.sealed_facts()]
    else:
        canon_view, sealed_terms, reveal, sealed_facts = "", [], None, []
    # Reported size of the denylist as loaded; the scan below may zero its local
    # copy, but the sidecar must not claim fewer terms were loaded than existed.
    sealed_terms_loaded = len(sealed_terms)

    findings: list[dict] = []
    seq = 0

    def add(f: dict):
        nonlocal seq
        seq += 1
        f["id"] = f"A{seq}"
        findings.append(f)

    # 1. Seal leaks (high trust; plant_hygiene policy — no always-on frame noise)
    sealed_phrases = []
    if parsed is not None:
        sealed_phrases = []
        for fact in sealed_facts:
            words = re.findall(r"[A-Za-z']+", fact)
            if len(words) >= 4:
                sealed_phrases.append(" ".join(words[:6]))
        if not sealed_facts:
            sealed_terms = []  # no sealed foundation → no seal findings
            sealed_phrases = []
    for leak in continuity_text.seal_leaks(
        chapter_text, sealed_terms, reveal, chapter_num, sealed_fact_phrases=sealed_phrases
    ):
        leak["chapters"] = [chapter_num]
        add(leak)
    # What the scan actually consulted (0 when there is no sealed foundation,
    # even though the denylist itself was non-empty — both facts are reported).
    sealed_terms_checked = len(sealed_terms) + len(sealed_phrases)

    # 2. Unknown entities (LOW trust — noisy by design)
    known_blob = continuity_text.known_entity_blob(
        characters_text, prior_text, canon_view
    )
    for uf in continuity_text.unknown_entities(chapter_text, known_blob):
        uf["chapters"] = [chapter_num]
        uf.setdefault("author_only", False)
        add(uf)

    # 3. Location-ish tokens vs world bible (low trust; warn-only)
    if world_text:
        for name in continuity_text.proper_nouns(chapter_text, min_len=4):
            if not re.search(rf"\b(?:the|at|in|to)\s+{re.escape(name)}\b", chapter_text):
                continue
            if re.search(rf"\b{re.escape(name)}\b", world_text):
                continue
            if re.search(rf"\b{re.escape(name)}\b", known_blob):
                continue
            add(continuity_text.make_finding(
                kind="location",
                claim=f"Place-like token {name!r} not in world bible / known context",
                chapters=[chapter_num],
                evidence=[name],
            ))

    # 4. Conservative canon contradiction hooks (false negatives OK)
    if parsed is not None:
        public_bullets = list(parsed.core_facts) + [
            f.fact for f in parsed.public_facts()
        ]
        for n, facts in parsed.as_of.items():
            if n < chapter_num:
                public_bullets.extend(facts)
        for bullet in public_bullets:
            if len(bullet) < 20:
                continue
            head = bullet.split("—")[0].split("-")[0].strip()
            tokens = [
                w for w in re.findall(r"[A-Za-z]{4,}", head)
                if w.lower() not in continuity_text.STOPWORDS
            ]
            if len(tokens) < 2:
                continue
            joined = " ".join(tokens[:4])
            if joined.lower() in chapter_text.lower():
                # Real gap quantifier — not a character class.
                neg = re.search(
                    rf"\b(?:not|never|no longer|wasn't|isn't)\b[^.]{{0,80}}{re.escape(tokens[0])}",
                    chapter_text,
                    re.IGNORECASE,
                )
                if neg:
                    add(continuity_text.make_finding(
                        kind="canon",
                        claim=f"Possible negation of canon claim {head[:80]!r}",
                        chapters=[chapter_num],
                        evidence=[neg.group(0)[:120], bullet[:120]],
                    ))

    # 5. Plant/harvest tags leaked into prose. Scanning goes through the tag
    # format owner (core.outline) so the pattern cannot drift from the parser's.
    try:
        for mark in outline_mod.scan_plant_tag_marks(chapter_text):
            add(continuity_text.make_finding(
                kind="plant",
                claim=f"Planning tag leaked into chapter prose: {mark}",
                chapters=[chapter_num],
                evidence=[mark],
            ))
    except Exception as e:
        print(f"WARN: continuity plant-tag scan failed for ch{chapter_num}: {e}", file=sys.stderr)

    result = {
        "chapter": chapter_num,
        "findings": findings,
        "trust_note": (
            "Checks are unequal trust. unknown_entity is low-trust/warn-only "
            "(high false positives). severity:structural is reserved/unused "
            "by gates today — everything behaves as warn."
        ),
        "reveal_chapter": reveal,
        "sealed_terms_loaded": sealed_terms_loaded,
        "sealed_terms_checked": sealed_terms_checked,
    }
    return result


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python pipeline/continuity_closed.py <chapter_num>", file=sys.stderr)
        return 2
    ch = int(sys.argv[1])
    result = run_closed_pass(ch)
    out_path = paths.get_eval_logs_dir() / f"continuity_ch{ch:02d}_closed.json"
    paths.save_json_atomic(result, out_path)
    n = len(result["findings"])
    print(f"continuity_closed ch{ch:02d}: {n} finding(s) -> {out_path}", file=sys.stderr)
    for f in result["findings"][:20]:
        print(f"  [{f['trust']}] {f['id']} {f['kind']}: {f['claim'][:100]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
