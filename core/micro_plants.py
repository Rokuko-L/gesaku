"""Prose-emergent micro-plant store and plant↔harvest matching helpers.

Distinct from outline-tag debts (`state["debts"]` / `[Plant: slug]`). These are
concrete details invented mid-draft that could pay off in a later chapter.
Store lives at projects/<name>/open_callbacks.json.
"""

from __future__ import annotations

import re
import uuid
from typing import Iterable

from core import paths

MAX_OPEN = 8
MAX_NEW_PER_CHAPTER = 1
DEFAULT_WINDOW = 12
# Two different questions, so two different thresholds — named, because a
# reader who sees "0.4" in one place and "0.55" in another cannot tell whether
# that is deliberate. Pairing a payoff to a setup tolerates looser wording than
# collapsing two setups that are meant to be the same thing.
_PLANT_LINK_OVERLAP = 0.40
_PLANT_LINK_MIN_SHARED = 2
_NEAR_DUP_OVERLAP = 0.55
_NEAR_DUP_MIN_SHARED = 2
# Storing a new plant asks "is this already on the list?" — a symmetric
# question, hence Jaccard rather than the overlap coefficient used above.
_DEDUPE_JACCARD = 0.55

_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "from", "by", "her", "his", "its", "is", "was", "are", "were",
    "be", "been", "being", "this", "that", "these", "those", "as", "it",
    "she", "he", "they", "them", "their", "our", "your", "my", "me", "you",
    "not", "no", "yes", "all", "any", "some", "into", "onto", "over", "under",
    "than", "then", "when", "where", "which", "who", "whom", "what", "how",
    "while", "after", "before", "during", "through", "about", "against",
    "between", "within", "without", "because", "if", "so", "than", "too",
    "very", "just", "also", "may", "might", "can", "could", "would", "should",
    "will", "shall", "do", "does", "did", "done", "has", "have", "had",
}


def content_tokens(text: str) -> set[str]:
    """Lowercase content-word set used for fuzzy plant/harvest matching."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) >= 3 and w not in _STOP}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap_coefficient(a: set[str], b: set[str]) -> float:
    """|A∩B| / min(|A|,|B|) — better than Jaccard for short-vs-long rewordings."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _should_link_plant_harvest(a: set[str], b: set[str]) -> bool:
    """Does this payoff plausibly resolve this setup?

    NB the overlap coefficient binds well before the shared-token floor on
    ~11-token descriptions: 2 shared words score ~0.18 and 4 score ~0.36, so
    in practice this asks for ~5 shared content words. Declared attributions
    (see ``declared_chapter``) exist precisely because that is a high bar for
    two independently written paraphrases.
    """
    shared = a & b
    if len(shared) < _PLANT_LINK_MIN_SHARED:
        return False
    return overlap_coefficient(a, b) >= _PLANT_LINK_OVERLAP


def _should_link_near_dup(a: set[str], b: set[str]) -> bool:
    shared = a & b
    if len(shared) < _NEAR_DUP_MIN_SHARED:
        return False
    return overlap_coefficient(a, b) >= _NEAR_DUP_OVERLAP


def load_callbacks(path=None) -> dict:
    """Load the open-callbacks store; empty structure if missing/corrupt."""
    p = path or paths.get_open_callbacks_path()
    empty = {"callbacks": [], "updated_chapter": None}
    if not p.exists():
        return empty
    try:
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    data.setdefault("callbacks", [])
    if not isinstance(data["callbacks"], list):
        data["callbacks"] = []
    return data


def save_callbacks(data: dict, path=None) -> None:
    paths.save_json_atomic(data, path or paths.get_open_callbacks_path())


def drop_source_chapter(data: dict, chapter: int) -> dict:
    """Remove plants extracted from a chapter that is about to be re-extracted."""
    data["callbacks"] = [
        c for c in data.get("callbacks", [])
        if c.get("source_chapter") != chapter
    ]
    return data


def expire_stale(data: dict, current_chapter: int, window: int = DEFAULT_WINDOW) -> dict:
    """Retire stale *nudges* — not the record that a plant exists.

    `expired` means "stop suggesting this in revision", which is why
    `mark_harvested` still accepts a payoff for an expired plant: the window is
    editorial policy, while whether a setup was ever paid off is a fact.
    """
    for c in data.get("callbacks", []):
        if c.get("status") != "open":
            continue
        planted = c.get("source_chapter") or 0
        # Honour the per-item window when one was stored.
        limit = c.get("window") or window
        if current_chapter - planted > limit:
            c["status"] = "expired"
            c["expired_chapter"] = current_chapter
    return data


def open_items(data: dict) -> list[dict]:
    return [c for c in data.get("callbacks", []) if c.get("status") == "open"]


def soft_inject_block(data: dict, chapter: int, limit: int = 3) -> str:
    """Optional revision-time callback list. Empty string when nothing open."""
    items = [
        c for c in open_items(data)
        if (c.get("source_chapter") or 0) < chapter
    ]
    items = items[:limit]
    if not items:
        return ""
    lines = "\n".join(
        f"  - (planted ch{c.get('source_chapter')}) {c.get('text', '')}"
        for c in items
    )
    return (
        "\n\nOPTIONAL CALLBACKS (prefer nothing over a forced reference):\n"
        "These concrete details were planted in earlier chapters. If ONE falls\n"
        "out naturally while rewriting, you may pay it off with changed meaning.\n"
        "Do NOT name-drop the object for its own sake. Leave them alone if forced.\n"
        f"{lines}\n"
    )


def add_plants(
    data: dict,
    chapter: int,
    plants: Iterable[dict],
    window: int = DEFAULT_WINDOW,
    max_new: int = MAX_NEW_PER_CHAPTER,
    max_open: int = MAX_OPEN,
) -> dict:
    """Append up to max_new new plants for a kept chapter; enforce open cap."""
    existing_tokens = {
        frozenset(content_tokens(c.get("text", ""))) for c in open_items(data)
    }
    added = 0
    for plant in plants:
        if added >= max_new:
            break
        if len(open_items(data)) >= max_open:
            break
        text = (plant.get("text") or "").strip()
        if not text:
            continue
        toks = content_tokens(text)
        if any(jaccard(toks, e) >= _DEDUPE_JACCARD for e in existing_tokens):
            continue
        item = {
            "id": uuid.uuid4().hex[:10],
            "text": text,
            "kind": (plant.get("kind") or "object").strip()[:24],
            "source_chapter": chapter,
            "status": "open",
            "window": window,
        }
        data.setdefault("callbacks", []).append(item)
        existing_tokens.add(frozenset(toks))
        added += 1
    return data


def mark_harvested(data: dict, chapter: int, harvested_ids: Iterable[str]) -> dict:
    """Record payoffs. Idempotent, and refused when they cannot be true.

    An `expired` plant can still be harvested: expiry only stops the revision
    nudge (see `expire_stale`). A payoff dated at or before the plant's own
    chapter is not a payoff — v4's store contains exactly that error
    (`a0e954db23`, planted ch23, harvested ch19).
    """
    ids = {str(i) for i in harvested_ids or []}
    for c in data.get("callbacks", []):
        if c.get("id") not in ids:
            continue
        if c.get("status") not in ("open", "expired"):
            continue
        planted = c.get("source_chapter") or 0
        if chapter <= planted:
            continue
        c["status"] = "harvested"
        c["harvested_chapter"] = chapter
    return data


def match_plant_harvest_threads(
    plants: list[dict],
    harvests: list[dict],
) -> list[dict]:
    """Cluster free-text plant/harvest bullets into coherent threads.

    plants/harvests: [{"text": str, "chapter": int}, ...]
    A harvest may also carry "declared_chapter": the chapter that set it up.
    A declared link is honoured without any similarity test, and the resulting
    thread reports "match": "declared" rather than "inferred".
    Returns [{"thread", "planted", "harvest", "match", "status"}, ...] sorted by
    earliest plant/harvest chapter.
    """
    nodes: list[dict] = []
    for kind, rows in (("plant", plants), ("harvest", harvests)):
        for row in rows:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            nodes.append({
                "kind": kind,
                "text": text,
                "chapter": int(row.get("chapter") or 0),
                "tokens": content_tokens(text),
                # A payoff may *declare* which chapter set it up. Declared
                # identity beats inferred similarity: it is the difference
                # between knowing and guessing.
                "declared": (int(row["declared_chapter"])
                             if kind == "harvest" and row.get("declared_chapter")
                             else None),
            })
    if not nodes:
        return []

    parent = list(range(len(nodes)))
    declared_roots: set[int] = set()

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            if a["kind"] == b["kind"]:
                if _should_link_near_dup(a["tokens"], b["tokens"]):
                    union(i, j)
            else:
                # plant may only pay off in a later (or same) chapter
                plant, harvest = (a, b) if a["kind"] == "plant" else (b, a)
                if (harvest["declared"] == plant["chapter"]
                        and plant["chapter"] <= harvest["chapter"]):
                    # The payoff named this chapter. No token test needed — but
                    # ordering still holds: a payoff cannot resolve a plant that
                    # has not happened yet.
                    union(i, j)
                    declared_roots.add(find(i))
                elif (plant["chapter"] <= harvest["chapter"]
                        and _should_link_plant_harvest(plant["tokens"], harvest["tokens"])):
                    union(i, j)

    clusters: dict[int, dict] = {}
    for i, node in enumerate(nodes):
        root = find(i)
        cl = clusters.setdefault(root, {
            "plants": [], "harvests": [], "texts": [],
        })
        cl["plants" if node["kind"] == "plant" else "harvests"].append(node["chapter"])
        cl["texts"].append(node["text"])

    threads = []
    for root, cl in clusters.items():
        planted = sorted(set(cl["plants"]))
        harvested = sorted(set(cl["harvests"]))
        # Prefer a harvest description as the label when available
        label = next(
            (t for t in cl["texts"] if t in [h["text"] for h in harvests if h["chapter"] in harvested]),
            cl["texts"][0],
        )
        if len(label) > 120:
            label = label[:117].rsplit(" ", 1)[0] + "…"
        threads.append({
            "thread": label,
            "planted": planted[0] if planted else None,
            "harvest": harvested[0] if harvested else None,
            "planted_all": planted,
            "harvested_all": harvested,
            # "declared" means a payoff named its plant; "inferred" means we
            # guessed from wording. The ledger must not confuse the two.
            "match": ("declared" if any(find(r) == root for r in declared_roots)
                      else "inferred"),
            "status": (
                "paid off" if planted and harvested
                else "recalled" if harvested
                else "open"
            ),
        })
    threads.sort(key=lambda t: (t["planted"] or t["harvest"] or 0, t["harvest"] or 9999))
    return threads
