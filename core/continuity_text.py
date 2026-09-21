"""Pure text helpers for closed-pass continuity checks. No project I/O."""

from __future__ import annotations

import re

# Trust metadata for Findings A — unequal by design (agentic-upgrades plan).
TRUST_BY_KIND = {
    "seal_leak": "high",
    "unknown_entity": "low",
    "location": "low",
    "canon": "medium",
    "plant": "medium",
}

# Title-Case or ALL-CAPS name runs. `[ \t]+` — never `\s+` (newlines must not
# glue a chapter title to the next sentence into a fake entity).
PROPER_NOUN_RE = re.compile(
    r"\b([A-Z]{2,}(?:[ \t]+[A-Z]{2,})*|[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)*)\b"
)
STOPWORDS = frozenset(
    """a an and are as at be but by for from has have he her his i it its is
    was were will with without this that these those there here when while
    who whom which what why how all any both each few more most other some
    such not no yes then than too very just also into onto about under over
    again chapter chapters the and if or nor so of on in to do does did
    term sealed fact facts uses before reveal quote reference
    my your our their his hers its this these those
    i you he she it we they me him them us
    doesn isn wasn weren didn don doesn't
    attendance homework reminders something across neat yeah like
    nothing meatloaf students try emotional orientation facts
    scene stakes summary plants location high reflection wrong""".split()
)

# Always-on meaning frames are too noisy for prose (plant_hygiene lesson).
# Closed-pass seal scanning uses multi-word sealed phrases only.
_ALWAYS_ON_FRAMES = frozenset(
    """secretly actually really truly real truth the mask front for puppet
    puppet-master fact reality never hidden identity agenda nature role
    protagonist red herring decoy""".split()
)


def _is_noisy_frame(term: str) -> bool:
    low = (term or "").lower().strip()
    if not low:
        return True
    if low in _ALWAYS_ON_FRAMES:
        return True
    words = low.split()
    return all(w in _ALWAYS_ON_FRAMES or w in STOPWORDS for w in words)


def proper_nouns(text: str, *, min_len: int = 2) -> list[str]:
    """Title-Case tokens not in the stopword set (order preserved, deduped)."""
    if not text:
        return []
    seen = set()
    out = []
    for m in PROPER_NOUN_RE.finditer(text):
        tok = m.group(1)
        if len(tok) < min_len or tok.lower() in STOPWORDS:
            continue
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
    return out


def known_entity_blob(
    characters_text: str,
    prior_chapters_text: str,
    canon_view_md: str,
) -> str:
    return "\n".join(x for x in (characters_text, prior_chapters_text, canon_view_md) if x)


def unknown_entities(
    chapter_text: str,
    known_blob: str,
    *,
    limit: int = 25,
) -> list[dict]:
    """Capitalized names in the chapter absent from known context.

    LOW trust — fires on one-off places and metaphors. Warn-only recall list.
    Always routed through make_finding so evidence[] is present.
    """
    findings = []
    for name in proper_nouns(chapter_text):
        if len(name) < 3:
            continue
        if name.lower() in STOPWORDS:
            continue
        if re.search(rf"\b{re.escape(name)}\b", known_blob):
            continue
        findings.append(make_finding(
            kind="unknown_entity",
            claim=f"Proper noun {name!r} not in registry/prior chapters/public canon",
            evidence=[name],
            author_only=False,
        ))
        if len(findings) >= limit:
            break
    return findings


def seal_leaks(
    chapter_text: str,
    sealed_terms: list[str],
    reveal_chapter: int | None,
    chapter_num: int,
    *,
    sealed_fact_phrases: list[str] | None = None,
) -> list[dict]:
    """Sealed material appearing in prose before the reveal.

    House policy (plant_hygiene): if there are no sealed facts, return [].
    Single-token always-on frames are never high-trust prose leaks.
    Multi-word denylist terms and sealed-fact phrases are the signal.
    """
    phrases = list(sealed_fact_phrases or [])
    multiword_terms = [
        t for t in (sealed_terms or [])
        if t and " " in t.strip() and not _is_noisy_frame(t)
    ]
    if not phrases and not multiword_terms:
        return []
    if reveal_chapter is not None and chapter_num >= reveal_chapter:
        return []
    leaks = []
    for term in multiword_terms:
        if re.search(rf"\b{re.escape(term)}\b", chapter_text, re.IGNORECASE):
            leaks.append({
                "kind": "seal_leak",
                "claim": f"Chapter uses sealed multi-word term {term!r} before reveal",
                "trust": TRUST_BY_KIND["seal_leak"],
                "severity": "warn",
                "author_only": True,
                "evidence": [term],
            })
    for phrase in phrases:
        if len(phrase.split()) < 3:
            continue
        if re.search(rf"\b{re.escape(phrase)}\b", chapter_text, re.IGNORECASE):
            leaks.append({
                "kind": "seal_leak",
                "claim": f"Chapter echoes sealed fact phrase: {phrase[:80]!r}",
                "trust": TRUST_BY_KIND["seal_leak"],
                "severity": "warn",
                "author_only": True,
                "evidence": [phrase[:120]],
            })
    return leaks


def make_finding(
    *,
    kind: str,
    claim: str,
    chapters: list[int] | None = None,
    evidence: list[str] | None = None,
    severity: str = "warn",
    finding_id: str = "",
    author_only: bool | None = None,
) -> dict:
    """Build a Findings-A record. severity:structural is reserved/unused by gates."""
    if author_only is None:
        author_only = kind == "seal_leak"
    return {
        "id": finding_id,
        "kind": kind,
        "claim": claim,
        "chapters": list(chapters or []),
        "evidence": list(evidence or []),
        "severity": severity if severity in ("warn", "structural") else "warn",
        "trust": TRUST_BY_KIND.get(kind, "low"),
        "author_only": bool(author_only),
    }


def _distinctive_tokens(findings_a: list[dict]) -> set[str]:
    """Proper-noun-like tokens from A claims/evidence; stopwords dropped."""
    toks = set()
    for f in findings_a or []:
        for blob in (f.get("claim") or "", *(f.get("evidence") or [])):
            for w in re.findall(r"[A-Za-z]{4,}", str(blob)):
                low = w.lower()
                if low in STOPWORDS:
                    continue
                toks.add(low)
    return toks


def _chapter_tokens(findings_a: list[dict]) -> set[int]:
    out = set()
    for f in findings_a or []:
        for ch in f.get("chapters") or []:
            try:
                out.add(int(ch))
            except (TypeError, ValueError):
                continue
    return out


def _tool_structured_fields(step: dict) -> tuple[set[int], str]:
    """(chapter ids, searchable text) from a tool-call trace entry."""
    inp = step.get("input") or {}
    chapters = set()
    if isinstance(inp, dict):
        for key in ("chapter", "n", "chapters"):
            if key not in inp:
                continue
            val = inp[key]
            if isinstance(val, list):
                for v in val:
                    try:
                        chapters.add(int(v))
                    except (TypeError, ValueError):
                        pass
            else:
                try:
                    chapters.add(int(val))
                except (TypeError, ValueError):
                    pass
        query = str(inp.get("query") or inp.get("q") or inp.get("name") or "")
    else:
        query = str(inp)
    return chapters, query.lower()


def overlap_a_tool_targets(findings_a: list[dict], trace: list[dict]) -> float:
    """Fraction of tool calls whose structured inputs touch Findings A.

    Prompt exclusion is not enforced — this post-hoc metric shows budget spent
    re-investigating A. Matches on:
      - chapter id equality (not substring; ch3 must not match 13)
      - distinctive claim/evidence tokens (len>=4, stopwords dropped) in query
    Non-blocking diagnostic. Does not use JSON-dump substring matching.
    """
    if not trace or not findings_a:
        return 0.0
    a_chapters = _chapter_tokens(findings_a)
    a_tokens = _distinctive_tokens(findings_a)
    if not a_chapters and not a_tokens:
        return 0.0
    hits = 0
    for step in trace:
        tool = step.get("tool") or ""
        if tool in ("(none)", "(parse)", "(transport)", "(stripped)", "(transcript)"):
            continue
        ch_ids, query = _tool_structured_fields(step)
        hit = False
        if a_chapters and ch_ids and (ch_ids & a_chapters):
            hit = True
        if not hit and a_tokens and query:
            q_words = set(re.findall(r"[a-z]{4,}", query))
            if q_words & a_tokens:
                hit = True
        if hit:
            hits += 1
    return round(hits / max(len(trace), 1), 3)


def json_dumps_safe(obj) -> str:
    try:
        import json
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)
