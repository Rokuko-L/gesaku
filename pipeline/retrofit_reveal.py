#!/usr/bin/env python3
"""
Post-reveal retrofit: rewrite chapters 1..R-1 after the reveal chapter lands.

Jobs (kept separate from inventing plants):
  - plants that already exist get thickened so they land in hindsight
  - the reveal is NOT pasted into early prose

Blocked when plant coverage is empty (nothing to work with).

Usage:
  python pipeline/retrofit_reveal.py            # auto-detect reveal from canon
  python pipeline/retrofit_reveal.py --chapter 14
  python pipeline/retrofit_reveal.py --dry-run  # coverage + continuity only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from core import canon as canon_mod
from core import paths
from core import plant_hygiene
from core import outline as outline_mod
from core import textstats
from core.genre import load_genre, prose_mode_system_block
from core.llm import call_llm
from core.paths import get_novel_title

load_dotenv()

RETROFIT_PROMPT = paths.load_prompt("retrofit_reveal")


def call_writer(prompt: str, max_tokens: int = 16000) -> str:
    genre_cfg = load_genre()
    system = genre_cfg["identity"]["revision_system"]
    perspective = genre_cfg.get("perspective", "")
    if perspective == "first_person":
        system += (
            "\n\nMANDATORY PERSPECTIVE: Keep STRICT FIRST-PERSON limited narration "
            "from the original POV character."
        )
    elif perspective == "third_person":
        system += (
            "\n\nMANDATORY PERSPECTIVE: Keep STRICT THIRD-PERSON limited narration "
            "anchored to the original POV character."
        )
    system += prose_mode_system_block(genre_cfg)
    return call_llm(
        prompt=prompt,
        system=system,
        model_key="writer",
        max_tokens=max_tokens,
        beta_context=True,
        timeout=600,
        temperature=0.7,
        raise_on_truncation=True,
    )


def load_text(path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def reveal_summary_md(parsed: canon_mod.ParsedCanon) -> str:
    sealed = parsed.sealed_facts()
    if not sealed:
        return "(no sealed facts)"
    return "\n".join(f"- {f.fact} (from ch {f.visible_from})" for f in sealed)


def plant_notes_for_chapter(chapter_text: str, names: list[str]) -> str:
    notes = []
    for name in names:
        for m in re.finditer(rf"\b{re.escape(name)}\b", chapter_text):
            start = max(0, m.start() - 80)
            end = min(len(chapter_text), m.end() + 120)
            snippet = chapter_text[start:end].replace("\n", " ").strip()
            notes.append(f"- {name}: …{snippet}…")
            break
    return "\n".join(notes) if notes else "(no prior plant snippets extracted)"


def continuity_report(
    parsed: canon_mod.ParsedCanon,
    outline_text: str,
    chapters: dict[int, str],
    reveal_chapter: int,
) -> dict:
    """Non-blocking author-continuity scan. Does not affect keep/discard."""
    report = {
        "reveal_chapter": reveal_chapter,
        "mask_breaks": [],
        "plant_gaps": [],
        "note": "informational only — never gates chapter keep",
    }
    denylist = canon_mod.sealed_denylist_terms(parsed)
    for n in sorted(chapters):
        if n >= reveal_chapter:
            continue
        body = chapters[n]
        if not body:
            continue
        for m in plant_hygiene.MEANING_FRAMES.finditer(body):
            report["mask_breaks"].append(
                f"ch{n}: meaning-shaped language {m.group(0)!r} before reveal"
            )
        low = body.lower()
        for term in denylist:
            if term in low and len(term) > 4:
                report["mask_breaks"].append(
                    f"ch{n}: sealed-term {term!r} in pre-reveal prose"
                )
    coverage = plant_hygiene.action_plant_coverage(
        outline_text,
        canon_mod.action_plant_characters(parsed, load_text(paths.get_characters_path())),
        reveal_chapter,
    )
    report["plant_gaps"] = coverage.get("problems", [])
    return report


def retrofit_chapter(
    chapter_num: int,
    reveal_chapter: int,
    parsed: canon_mod.ParsedCanon,
    plant_names: list[str],
    dry_run: bool = False,
) -> bool:
    chapters_dir = paths.get_chapters_dir()
    ch_path = chapters_dir / f"ch_{chapter_num:02d}.md"
    if not ch_path.exists():
        print(f"  skip ch{chapter_num}: no draft", file=sys.stderr)
        return False
    text = ch_path.read_text(encoding="utf-8")
    wc = len(text.split())

    outline_text = load_text(paths.get_outline_path())
    try:
        chapter_outline = outline_mod.extract_chapter_outline(outline_text, chapter_num) \
            if hasattr(outline_mod, "extract_chapter_outline") else ""
    except Exception:
        chapter_outline = ""
    if not chapter_outline:
        # Local extract (draft_chapter owns the detailed-section helper)
        m = re.search(
            rf"###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{chapter_num}\b.*?(?=###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*\d+\b|## Act|## Foreshadowing|$)",
            outline_text,
            re.IGNORECASE | re.DOTALL,
        )
        chapter_outline = m.group(0).strip() if m else "(outline entry missing)"

    if dry_run:
        print(f"  [dry-run] would retrofit ch{chapter_num} ({wc}w)", file=sys.stderr)
        return True

    prompt = RETROFIT_PROMPT.format(
        reveal_chapter=reveal_chapter,
        chapter_num=chapter_num,
        reveal_summary=reveal_summary_md(parsed),
        chapter_text=text,
        chapter_outline=chapter_outline,
        plant_notes=plant_notes_for_chapter(text, plant_names),
        voice=load_text(paths.get_voice_path()),
    )
    print(f"Retrofitting Chapter {chapter_num}...", file=sys.stderr)
    result = call_writer(prompt)
    new_wc = len(result.split())
    if abs(new_wc - wc) / max(wc, 1) > 0.35:
        print(
            f"  WARN: ch{chapter_num} length drifted {wc} -> {new_wc}; keeping original",
            file=sys.stderr,
        )
        return False
    ch_path.write_text(outline_mod.normalize_chapter_heading(result, chapter_num), encoding="utf-8")
    print(f"  Saved ch{chapter_num} ({wc}w -> {new_wc}w)", file=sys.stderr)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-reveal retrofit of pre-reveal chapters")
    parser.add_argument("--chapter", type=int, default=None, help="Reveal chapter (default: from canon)")
    parser.add_argument("--dry-run", action="store_true", help="Coverage/continuity only, no rewrite")
    args = parser.parse_args()

    canon_text = load_text(paths.get_canon_path())
    outline_text = load_text(paths.get_outline_path())
    characters_text = load_text(paths.get_characters_path())
    parsed = canon_mod.parse_canon(canon_text)
    reveal = args.chapter or parsed.reveal_chapter()
    if not reveal or reveal <= 1:
        print("No sealed reveal chapter in canon — nothing to retrofit.", file=sys.stderr)
        return 0

    plant_names = canon_mod.action_plant_characters(parsed, characters_text)
    coverage = plant_hygiene.action_plant_coverage(outline_text, plant_names, reveal)
    side_path = paths.get_project_dir() / "retrofit_report.json"
    chapters = {}
    chapters_dir = paths.get_chapters_dir()
    for p in sorted(chapters_dir.glob("ch_*.md")):
        m = re.match(r"ch_(\d+)\.md", p.name)
        if m:
            chapters[int(m.group(1))] = p.read_text(encoding="utf-8")

    cont = continuity_report(parsed, outline_text, chapters, reveal)
    report = {
        "reveal_chapter": reveal,
        "coverage": coverage,
        "continuity": cont,
        "retrofitted": [],
        "blocked": False,
        "reason": "",
    }

    if not coverage.get("pass", False) and plant_names:
        report["blocked"] = True
        report["reason"] = "under-planted: " + "; ".join(coverage.get("problems", []))
        print(f"RETROFIT BLOCKED — {report['reason']}", file=sys.stderr)
        side_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 2

    for n in range(1, reveal):
        if retrofit_chapter(n, reveal, parsed, plant_names, dry_run=args.dry_run):
            if not args.dry_run:
                report["retrofitted"].append(n)

    side_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"Retrofit {'dry-run ' if args.dry_run else ''}complete for ch1..{reveal - 1}; "
        f"report: {side_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
