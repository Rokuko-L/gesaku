#!/usr/bin/env python3
"""Generate webui fixtures from a REAL project directory.

Usage: uv run python webui/fixtures.py [project_dir_name]

Reads world/characters/canon/outline files and emits JSON shaped exactly
like contract.js types into webui/frontend/src/fixtures/. The mock client
then serves real novel data with zero screen changes.
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "webui" / "frontend" / "src" / "fixtures"

DEFAULT_PROJECT = "sir the confortable v3"


def clean(t: str) -> str:
    t = t.replace("**", "").replace("*", "").strip()
    return re.sub(r"\s+", " ", t)


def split_h3(md: str):
    """Split '### **Title**' headings -> [(title, body)]."""
    pat = re.compile(r"^###\s+\*\*(.+?)\*\*\s*$", re.M)
    ms = list(pat.finditer(md))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(md)
        out.append((clean(m.group(1)), md[m.end():end]))
    return out


# Character registry entries: '## **1. NAME**' or '### **A. NAME (role)**' —
# an ordinal prefix is required so subsections ('### **Personality & Flaws**')
# are excluded and stay inside the owning character's body block.
CHAR_HEAD = re.compile(
    r"^#{2,3}\s+\*\*(?:([A-Za-z]|\d+)[\.\)]\s+)?(.+?)\*\*\s*$", re.M)


# Registry section headers that pattern-match a character entry but aren't one
SECTION_WORDS = ("supporting characters", "replacement behaviors", "themes",
                 "character arcs", "registry", "antagonists", "minor characters")


def display_name(raw: str) -> str:
    """Role prefixes ('THE NARRATOR: DANIEL VEY') and ALL-CAPS names cleaned."""
    name = raw.split(":", 1)[1].strip() if ": " in raw and raw.index(":") < 40 else raw
    probe = name.split("(")[0]
    if probe.isupper():
        name = name.title()
    return name


HONORIFICS = {"king", "queen", "prince", "princess", "lady", "lord",
              "sir", "archmage", "the", "master", "dame", "captain",
              "ms", "mr", "mrs", "dr"}


def key_name(full: str) -> str:
    """First real given name, skipping honorific prefixes."""
    s = full.split(",")[0].split("(")[0].replace('"', "")
    words = [w for w in s.split() if w]
    if words and words[0].lower().rstrip(".") in HONORIFICS and len(words) > 1:
        words = words[1:]
    # stop at prepositions: 'Eris of the Spire' -> 'Eris'
    out = []
    for w in words:
        if w.lower() in {"of", "the"} and out:
            break
        out.append(w)
    return out[0] if out else full


def short_name(full: str) -> str:
    """Compact display name: strip honorifics tail after comma/paren."""
    return key_name(full)


def gen_foundation(p: Path, state: dict) -> dict:
    docs = {}
    for n in ("world", "characters", "canon", "voice"):
        f = p / f"{n}.md"
        docs[n] = f.read_text(encoding="utf-8") if f.exists() else ""

    # chapter texts for mention maps (id -> text)
    ch_texts = {}
    for f in sorted(p.glob("chapters/ch_*.md")):
        num = int(re.search(r"ch_(\d+)", f.name).group(1))
        ch_texts[num] = f.read_text(encoding="utf-8")

    def mentions_for(aliases) -> list:
        hits = []
        for num, txt in ch_texts.items():
            if any(len(a) >= 3 and re.search(rf"\b{re.escape(a)}\b", txt) for a in aliases):
                hits.append(f"ch_{num:02d}")
        return hits[:12]

    # -- character nodes ------------------------------------------------
    nodes, char_blocks = [], []
    heads = [m for m in CHAR_HEAD.finditer(docs["characters"]) if m.group(1)]
    for idx, m in enumerate(heads):
        full_name = m.group(2)
        if any(w in full_name.lower() for w in SECTION_WORDS):
            continue
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(docs["characters"])
        kn = key_name(display_name(full_name))
        if not kn:
            continue
        # aliases: quoted nicknames ('Daniel "Danny" Vey' -> also matches 'Danny')
        # handles both straight and typographic quotes
        aliases = {kn, *re.findall(r"[\"“]([^\"”]+)[\"”]", full_name)}
        aliases = {a if not a.isupper() else a.title() for a in aliases}
        body = docs["characters"][m.end():end]
        status = None
        if re.search(r"\bdead\b|deceased|\bkilled\b", body.lower() + m.group(0).lower()):
            status = "dead?"
        elif re.search(r"\bmissing\b|\bunknown\b|presumed", body.lower()):
            status = "unknown"
        desc = clean(body)
        nodes.append({
            "id": f"c{idx + 1}", "label": kn,
            "kind": "character", "status": status,
            "desc": desc[:400] + ("…" if len(desc) > 400 else ""),
            "mentions": mentions_for(aliases),
        })
        char_blocks.append((kn, aliases, body))

    # edges: co-mention between character blocks (real relationships!)
    edges, seen = [], set()
    for a_name, _, a_body in char_blocks:
        for b_name, b_aliases, _ in char_blocks:
            if a_name == b_name:
                continue
            mentioned = any(
                re.search(rf"\b{re.escape(al)}\b", a_body)
                for al in b_aliases if len(al) >= 3)
            if mentioned:
                pair = tuple(sorted((a_name, b_name)))
                if pair not in seen:
                    seen.add(pair)
                    edges.append({
                        "from": next(n["id"] for n in nodes if n["label"] == a_name),
                        "to": next(n["id"] for n in nodes if n["label"] == b_name),
                        "label": "entangled",
                    })

    # -- factions & locations from world.md ------------------------------
    def bullets_under(marker: str, kind: str, limit=5):
        """Faction/location nodes from world.md bullet lists; returns {label: desc}."""
        out = {}
        sec = re.search(
            rf"##\s+\*\*[^*]*{marker}[^*]*\*\*(.*?)(?=\n##\s|\Z)",
            docs["world"], re.I | re.S)
        if not sec:
            return out
        for b, desc in re.findall(r"^-\s+\*\*(.+?)\*\*:?\s*(.*)$",
                                  sec.group(1), re.M):
            label = clean(b).rstrip(":").strip()
            # skip bullets that duplicate a character entry ("The Cult of the
            # Eternal Flame" when 'Cult' is already a registry node)
            if any(w in cn.lower() or cn in label.lower()
                   for w in label.lower().split() if len(w) > 3
                   for cn, _, _ in char_blocks):
                continue
            if sum(1 for n in nodes if n["label"] == label) or \
               sum(1 for n in nodes if n["kind"] == kind) >= limit:
                continue
            nodes.append({"id": f"{kind}{len(nodes)}", "label": label,
                          "kind": kind, "status": None})
            out[label] = clean(desc)
        return out

    faction_descs = bullets_under("POWER GROUPS", "faction")
    location_descs = bullets_under("LOCATION", "location")

    bullets_under("POWER GROUPS", "faction")
    bullets_under("LOCATION", "location")

    # faction/location <-> character edges: character named in the entity's
    # world.md description, or entity named in a character block (real data)
    by_label = {n["label"]: n["id"] for n in nodes}
    for label, desc in {**faction_descs, **location_descs}.items():
        nid = by_label[label]
        for cn, aliases, _ in char_blocks:
            if any(len(a) >= 3 and re.search(rf"\b{re.escape(a)}\b", desc, re.I)
                   for a in aliases):
                edges.append({"from": nid, "to": by_label[cn], "label": "entangled"})
    for n in [x for x in nodes if x["kind"] in ("faction", "location")]:
        core = n["label"].replace("The ", "").split(":")[0].strip()
        if len(core) < 4:
            continue
        for cn, _, cb in char_blocks:
            if re.search(rf"\b{re.escape(core)}\b", cb, re.I):
                pair = tuple(sorted((n["id"], by_label[cn])))
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append({"from": n["id"], "to": by_label[cn],
                              "label": "entangled"})

    # attach faction/location descriptions + chapter mentions
    for label, desc in {**faction_descs, **location_descs}.items():
        nid = by_label[label]
        for n in nodes:
            if n["id"] == nid:
                d = clean(desc)
                n["desc"] = d[:400] + ("…" if len(d) > 400 else "")
                n["mentions"] = mentions_for([label])
                break

    return {
        "meta": {
            "title": state.get("title", "Untitled"),
            "score": state.get("foundation_score"),
            "lore": state.get("lore_score"),
            "chaptersTotal": state.get("chapters_total"),
            "phase": state.get("phase"),
        },
        "docs": docs,
        "entities": {"nodes": nodes, "edges": edges},
    }


def _cluster_threads_from_outline_bullets(txt: str) -> list:
    """Rebuild clustered threads from per-chapter **Plants:** / **Harvests:** bullets.

    Used when outline.md still has a legacy unclustered table (or none).
    """
    from core.micro_plants import match_plant_harvest_threads

    plants, harvests = [], []
    current_ch = None
    mode = None
    for line in txt.splitlines():
        m = re.match(r"^###\s+(?:Ch|Chapter)\.?\s*(\d+)\b", line, re.I)
        if m:
            current_ch = int(m.group(1))
            mode = None
            continue
        if current_ch is None:
            continue
        low = line.strip().lower()
        if low.startswith("**plants:**"):
            mode = "plant"
            continue
        if low.startswith("**harvests:**"):
            mode = "harvest"
            continue
        if line.strip().startswith("**") or line.strip().startswith("###"):
            mode = None
            continue
        if mode and line.strip().startswith("- "):
            text = clean(line.strip()[2:])
            if not text:
                continue
            row = {"text": text, "chapter": current_ch}
            if mode == "harvest":
                # "…" bullets written by build_outline carry the chapter whose
                # plant this payoff resolves, so identity comes off the file
                # rather than being re-inferred from wording.
                declared = re.match(r"\[payoff of ch(\d+)\]\s*(.*)", text, re.I)
                if declared:
                    row["declared_chapter"] = int(declared.group(1))
                    row["text"] = declared.group(2).strip()
                    if not row["text"]:
                        continue
            (plants if mode == "plant" else harvests).append(row)
    if not plants and not harvests:
        return []
    return match_plant_harvest_threads(plants, harvests)


_SLUGLIKE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)+$")


def _thread_row(thread: str, planted, harvest, planted_all=None, harvested_all=None,
                match_method: str | None = None) -> dict:
    """One ledger row, described factually.

    status: matched (plant and payoff), plant-only, harvest-only.
    match_method: "slug" when the row's identity is a stable slug, else
      "inferred" — the pairing came from token overlap, not declared identity.
    span: chapters from the earliest plant to the latest payoff, or None.
    """
    if planted is not None and harvest is not None:
        status = "matched"
    elif planted is not None:
        status = "plant-only"
    else:
        status = "harvest-only"

    span = None
    if status == "matched":
        p_all = planted_all or [planted]
        h_all = harvested_all or [harvest]
        span = max(h_all) - min(p_all)

    if match_method is None:
        match_method = "slug" if _SLUGLIKE.match(thread.strip()) else "inferred"

    return {
        "thread": thread,
        "planted": planted,
        "harvest": harvest,
        "status": status,
        "matchMethod": match_method,
        "span": span,
    }


def gen_ledger(p: Path, state: dict) -> dict:
    total = state.get("chapters_total", 24)
    # A finished book is a different reading than one still being written:
    # an unpaid plant there is a promise the novel shipped without.
    settled = (state.get("current_focus") == "done"
               or state.get("phase") == "complete")

    premise = []
    part1 = p / ".outline_part1.md"
    if part1.exists():
        txt = part1.read_text(encoding="utf-8")
        sec = re.search(r"\*\*PREMISE BEATS:\*\*(.*?)(?=\n\*\*|\n#)", txt, re.S)
        if sec:
            for label in re.findall(r"^- \*\*\d+\.\s*(.+?):", sec.group(1), re.M):
                premise.append({"label": clean(label)})

    roadmap = []
    rm = p / ".outline_roadmap.md"
    if rm.exists():
        txt = rm.read_text(encoding="utf-8")
        # Split on the headings rather than matching to the next heading: the
        # old `\n+` + lookahead swallowed the blank line, so alternate chapters
        # were absorbed into the previous entry and their numbers went missing.
        parts = re.split(r"^###\s+Chapter\s+(\d+)\s*:?[ \t]*([^\n]*)",
                         txt, flags=re.MULTILINE)
        for i in range(1, len(parts) - 2, 3):
            num, heading, body = int(parts[i]), clean(parts[i + 1]), parts[i + 2].strip()
            # "### Chapter 1: [ordinary_world] Teen Demon Lord Baal II…"
            tag = re.match(r"^\[([^\]]+)\]\s*(.*)$", heading)
            if tag:
                title, lead = clean(tag.group(1)).replace("_", " "), tag.group(2).strip()
            else:
                title, lead = heading.replace("_", " ") or f"chapter {num}", ""
            prose_body = (lead + " " + body).strip()
            sentences = re.split(r"(?<=[.!?])\s+", prose_body)
            roadmap.append({
                "chapter": num,
                "title": title,
                "beats": [clean(s) for s in sentences[:3] if clean(s)],
            })
        roadmap.sort(key=lambda c: c["chapter"])

    threads = []
    outline = p / "outline.md"
    if outline.exists():
        txt = outline.read_text(encoding="utf-8")
        clustered = _cluster_threads_from_outline_bullets(txt)
        if clustered:
            for t in clustered:
                threads.append(_thread_row(
                    t["thread"], t["planted"], t["harvest"],
                    t.get("planted_all"), t.get("harvested_all"),
                    match_method=("declared" if t.get("match") == "declared"
                                  else None),
                ))
        else:
            # the ledger table cells are hard-truncated at 60 chars by older
            # pipeline builds; recover full thread text from outline bullets when possible
            bullets = re.findall(r"^- (.+)$", txt, re.M)
            sec = re.search(r"FORESHADOWING LEDGER\n(.*?)(?=\n\s*\n(?!\s*\|)|\Z)", txt, re.S)
            if sec:
                # Support both 3-col (legacy) and 4-col (clustered) tables
                for row in re.findall(
                        r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*(?:\|\s*([^|]*?)\s*)?\|$",
                        sec.group(1), re.M):
                    thread, planted, harvested, status_cell = row
                    if thread.strip().lower() in ("thread", "--------", ""):
                        continue
                    if set(thread.strip()) <= set("-: "):
                        continue
                    pm = re.search(r"[Cc]h\.?\s*(\d+)", planted)
                    hm = re.search(r"[Cc]h\.?\s*(\d+)", harvested)
                    if not pm and not hm:
                        continue
                    label = clean(thread)
                    if len(label) >= 58:
                        full = next((b for b in bullets
                                     if clean(b).startswith(label[:40])), None)
                        if full:
                            label = clean(full)
                        else:
                            label = label.rsplit(" ", 1)[0] + "…"
                    threads.append(_thread_row(
                        label,
                        int(pm.group(1)) if pm else None,
                        int(hm.group(1)) if hm else None,
                    ))
    threads.sort(key=lambda t: (t["planted"] or t["harvest"] or 0,
                                t["harvest"] or 9999))

    # Planned major threads from the foundation roadmap (the real craft arcs).
    # The roadmap writes these as "**1. slug**", not "### slug" — parsing only
    # the latter left this section permanently empty, hiding every long arc.
    planned = []
    if rm.exists():
        rtxt = rm.read_text(encoding="utf-8")
        sec = re.search(
            r"GLOBAL PLOT THREADS LEDGER\n(.*?)(?=\n## |\Z)", rtxt, re.S | re.I)
        if sec:
            blocks = re.split(r"\n(?=\*\*\d+\.|\*\*[a-zA-Z0-9_]+|###\s)", sec.group(1))
            for block in blocks:
                hm = re.match(
                    r"(?:###\s+)?\**\s*(?:\d+\.\s*)?([a-zA-Z0-9_]+)\s*\**", block.strip())
                if not hm:
                    continue
                slug = hm.group(1)
                # Planted/Harvested lines routinely name several chapters; the
                # arc is earliest plant -> latest payoff.
                planted_text = (block.split("Planted:")[1].split("Harvested:")[0]
                                if "Planted:" in block else "")
                planted_chs = [int(n) for n in
                               re.findall(r"Chapter\s*(\d+)", planted_text, re.I)]
                harvest_text = block.split("Harvested:")[1] if "Harvested:" in block else ""
                harvest_chs = [int(n) for n in
                               re.findall(r"Chapter\s*(\d+)", harvest_text, re.I)]
                planned.append(_thread_row(
                    slug.replace("_", " "),
                    min(planted_chs) if planted_chs else None,
                    max(harvest_chs) if harvest_chs else None,
                    planted_chs or None,
                    harvest_chs or None,
                    match_method="slug",
                ))

    # Prose-emergent micro-plants (open_callbacks.json), if the extractor has run
    callbacks = []
    cb_path = p / "open_callbacks.json"
    if cb_path.exists():
        try:
            cb = json.loads(cb_path.read_text(encoding="utf-8"))
            for c in cb.get("callbacks") or []:
                callbacks.append({
                    "id": c.get("id"),
                    "text": c.get("text", ""),
                    "kind": c.get("kind", "object"),
                    "sourceChapter": c.get("source_chapter"),
                    "harvestChapter": c.get("harvested_chapter"),
                    "status": c.get("status", "open"),
                })
        except (OSError, ValueError):
            pass

    return {
        "premiseBeats": premise,
        "roadmap": roadmap,
        "threads": threads,
        "plannedThreads": planned,
        "callbacks": callbacks,
        "chaptersTotal": total,
        "settled": settled,
    }


def gen_projects(state: dict, p: Path) -> list:
    items = []
    for d in sorted((ROOT / "projects").iterdir()):
        sf = d / "state.json"
        if not d.is_dir() or not sf.exists():
            continue
        try:
            s = json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            continue
        manuscript = d / "manuscript.md"
        words = len(manuscript.read_text(encoding="utf-8").split()) if manuscript.exists() else 0
        phase = s.get("phase", "idle") or "idle"
        if phase.startswith("complete"):
            phase = "export"
        genre = None
        ag = d / "active_genre.json"
        if ag.exists():
            try:
                genre = json.loads(ag.read_text(encoding="utf-8")).get("genre_name")
            except Exception:
                pass
        items.append({
            "name": d.name,
            "title": s.get("title", "Untitled"),
            "genre": genre,
            "phase": "idle" if s.get("current_focus") == "done" else phase,
            # `phase` collapses "finished" and "never ran" into idle; this keeps
            # the distinction available to the shelf and the header.
            "finished": s.get("current_focus") == "done",
            "foundationScore": s.get("foundation_score", 0) or 0,
            "novelScore": s.get("novel_score"),
            "bestNovelScore": s.get("best_novel_score"),
            "revisionCycle": s.get("revision_cycle", 0),
            "chaptersTotal": s.get("chapters_total", 0) or 0,
            "chaptersDone": s.get("chapters_drafted", 0) or 0,
            "words": words,
            "updatedAt": None,
            "running": False,
            "_primary": d.name == p.name,
        })
    return items


def md_section(md: str, name: str) -> str:
    """Body of a '## NAME' section (## PROBLEM / WHAT TO KEEP / WHAT TO CHANGE)."""
    m = re.search(rf"^##\s+.*?{name}.*?$(.*?)(?=^##\s|\Z)", md, re.I | re.M | re.S)
    return m.group(1).strip() if m else ""


def gen_chapters(p: Path, state: dict) -> list:
    """chapters/ch_XX.md + attempt history from results.tsv."""
    attempts_by_ch = {}
    results = p / "results.tsv"
    if results.exists():
        lines = results.read_text(encoding="utf-8").splitlines()[1:]
        for line in lines:
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            commit, phase, score, words, status, _desc = parts[:6]
            m = re.match(r"ch(\d+)", phase)
            if not m:
                continue
            attempts_by_ch.setdefault(int(m.group(1)), []).append({
                "score": float(score), "words": int(words), "status": status,
            })

    chapters = []
    for f in sorted(p.glob("chapters/ch_*.md")):
        num = int(re.search(r"ch_(\d+)", f.name).group(1))
        raw = f.read_text(encoding="utf-8")
        tm = re.match(r"#\s*Chapter\s*\d+:?\s*(.+)", raw)
        title = clean(tm.group(1)) if tm else f"chapter {num}"
        atts = attempts_by_ch.get(num, [])
        kept = next((a for a in atts if a["status"] == "keep"), None)
        chapters.append({
            "num": num,
            "id": f"ch_{num:02d}",
            "title": title,
            "words": len(raw.split()),
            "status": "kept" if kept else ("discarded" if atts else "pending"),
            "score": kept["score"] if kept else None,
            "attempts": atts,
            "prose": raw,
        })
    return chapters


EVAL_DIMS = ("voice_adherence", "beat_coverage", "character_voice",
             "prose_quality", "engagement", "continuity", "reader_grounding")


def gen_evals(p: Path) -> dict:
    """eval_logs/*.json -> { ch_01: EvalAttempt[], _foundation: EvalAttempt[] }."""
    out = {}
    for f in sorted(p.glob("eval_logs/*.json")):
        m = re.match(r"(\d{8}_\d{6})_(ch\d+|foundation)\.json", f.name)
        if not m:
            continue
        ts, target = m.group(1), m.group(2)
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        dims = {}
        for k in EVAL_DIMS:
            v = d.get(k)
            if isinstance(v, dict):
                dims[k] = {
                    "score": v.get("score"),
                    "weakestMoment": v.get("weakest_moment", ""),
                    "fix": v.get("fix", ""),
                    "note": v.get("note", ""),
                }
        slop = d.get("slop") or {}
        att = {
            "ts": ts,
            "overall": d.get("overall_score"),
            "rawJudge": d.get("raw_judge_score"),
            "weakest": d.get("weakest_dimension", ""),
            "lengthPenalty": d.get("length_penalty", 0.0) or 0.0,
            "orientationPenalty": d.get("orientation_penalty", 0.0) or 0.0,
            "slopPenalty": slop.get("slop_penalty", 0.0) or 0.0,
            "dims": dims,
            "strongest": d.get("three_strongest_sentences", []),
            "weakestSentences": d.get("three_weakest_sentences", []),
            "aiPatterns": d.get("ai_patterns_detected", []),
            "topRevisions": d.get("top_3_revisions", []),
            "newCanon": len(d.get("new_canon_entries") or []),
            "unexplained": len(d.get("unexplained_references") or []),
        }
        out.setdefault(target, []).append(att)
    return out


def gen_revision(p: Path) -> dict:
    """briefs/*.md + edit_logs/chNN_cuts.json + edit_logs/*_review.json."""
    briefs = []
    for f in sorted(p.glob("briefs/ch*.md")):
        m = re.match(r"ch(\d+)_(\w+)\.md", f.name)
        if not m:
            continue
        raw = f.read_text(encoding="utf-8")
        tm = re.match(r"#\s+(.+)", raw)
        briefs.append({
            "chapter": int(m.group(1)),
            "kind": m.group(2),
            "title": clean(tm.group(1)) if tm else f.name,
            "problem": md_section(raw, "PROBLEM"),
            "keep": md_section(raw, "WHAT TO KEEP"),
            "directives": md_section(raw, "WHAT TO CHANGE"),
        })

    cuts = {}
    for f in sorted(p.glob("edit_logs/ch*_cuts.json")):
        ch = re.search(r"ch(\d+)_cuts", f.name).group(1)
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        cuts[f"ch_{int(ch):02d}"] = [
            {"quote": c.get("quote", ""), "type": c.get("type", ""),
             "reason": c.get("reason", ""), "action": c.get("action", ""),
             "rewrite": c.get("rewrite")}
            for c in d.get("cuts", [])
        ]

    reviews = []
    for f in sorted(p.glob("edit_logs/*_review.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        reviews.append({
            "ts": d.get("timestamp", ""),
            "stars": d.get("stars"),
            "summary": d.get("critic_summary", ""),
            "items": [
                {"number": it.get("number"), "title": it.get("title", ""),
                 "severity": it.get("severity", ""), "suggestion": it.get("suggestion", "")}
                for it in d.get("professor_items", [])
            ],
            "majorItems": d.get("major_items", 0),
            "totalItems": d.get("total_items", 0),
        })

    return {"briefs": briefs, "cuts": cuts, "reviews": reviews}


def gen_tournament(p: Path) -> list:
    """Synthesize A/B matches from results.tsv chapters with discard+keep attempts.

    The discarded attempt's prose is not on disk, so variant A uses a mid-slice
    of the kept chapter's prose — real sentences, distinct from variant B.
    """
    matches = []
    for ch in gen_chapters(p, {}):
        atts = ch["attempts"]
        kept = next((a for a in atts if a["status"] == "keep"), None)
        dropped = [a for a in atts if a["status"] == "discard"]
        if not kept or not dropped:
            continue
        paras = [q for q in ch["prose"].split("\n\n") if q.strip()]
        if len(paras) < 8:
            continue
        mid = len(paras) // 2
        prose_a = "\n\n".join(paras[mid:mid + 4])
        prose_b = "\n\n".join(paras[:4])
        # elo-ish seeding from attempt scores, so bars are data-derived
        elo = lambda s: round(1350 + s * 10)
        d = dropped[-1]
        matches.append({
            "id": f"match_{len(matches) + 1:03d}",
            "chapter": ch["num"],
            "a": {"label": "variant_a", "score": d["score"], "words": d["words"],
                  "elo": elo(d["score"]), "prose": prose_a},
            "b": {"label": "variant_b", "score": kept["score"], "words": kept["words"],
                  "elo": elo(kept["score"]), "prose": prose_b, "kept": True},
        })
    return matches


def main():
    proj_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROJECT
    p = ROOT / "projects" / proj_name
    state = json.loads((p / "state.json").read_text(encoding="utf-8"))

    OUT.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "foundation.json": gen_foundation(p, state),
        "ledger.json": gen_ledger(p, state),
        "projects.json": gen_projects(state, p),
        "chapters.json": gen_chapters(p, state),
        "evals.json": gen_evals(p),
        "revision.json": gen_revision(p),
        "tournament.json": gen_tournament(p),
    }
    for name, data in fixtures.items():
        (OUT / name).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    print(f"fixtures regenerated from '{proj_name}'")


if __name__ == "__main__":
    main()
