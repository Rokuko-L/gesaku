#!/usr/bin/env python3
"""Extract prose-emergent micro-plants after a chapter is kept.

Fail-soft: never blocks drafting. Usage:
    python pipeline/extract_micro_plants.py <chapter> [--reextract]
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from core import llm, paths, textstats  # noqa: E402
from core import micro_plants as mp  # noqa: E402
from core.llm import call_llm  # noqa: E402
from core.validation import MicroPlantExtract, OutputValidationError, parse_validated  # noqa: E402


def _excerpt(chapter_text: str) -> str:
    """The whole chapter.

    This used to send the first 400 and last 800 words of any chapter over
    1200 words, which made a payoff landing mid-chapter invisible: the extractor
    never saw it, so the plant stayed "open" forever. Chapters here run a few
    thousand words — comfortably one call.
    """
    return chapter_text


def extract_for_chapter(chapter: int, reextract: bool = False) -> int:
    """Update open_callbacks.json for a kept chapter. Returns 0 on success/skip."""
    chapter_path = paths.get_chapters_dir() / f"ch_{chapter:02d}.md"
    if not chapter_path.exists():
        print(f"micro-plants: ch{chapter:02d} missing — skip", file=sys.stderr)
        return 0

    data = mp.load_callbacks()
    if reextract:
        data = mp.drop_source_chapter(data, chapter)

    open_list = mp.open_items(data)
    if not open_list:
        open_block = "(none)"
    else:
        open_block = "\n".join(
            f"- {c.get('id')} — {c.get('text')} (planted ch{c.get('source_chapter')})"
            for c in open_list
        )

    template = paths.load_prompt("extract_micro_plants")
    prompt = template.format(
        chapter=chapter,
        excerpt=_excerpt(chapter_path.read_text(encoding="utf-8")),
        open_list=open_block,
    )

    raw = None
    last_err = None
    for attempt in range(1, 3):
        try:
            raw = call_llm(
                prompt=prompt,
                system="You extract concrete callback candidates from novel chapters. JSON only.",
                model_key="judge",
                max_tokens=800,
                temperature=0.1,
                timeout_role="short",
            )
            break
        except Exception as e:
            last_err = e
            print(f"micro-plants: extract attempt {attempt} failed: {e}", file=sys.stderr)
    if raw is None:
        print(f"micro-plants: giving up on ch{chapter:02d}: {last_err}", file=sys.stderr)
        return 0

    try:
        parsed = parse_validated(MicroPlantExtract, raw, context=f"micro-plants ch{chapter}")
    except OutputValidationError as e:
        print(f"micro-plants: schema fail ch{chapter:02d}: {e.feedback}", file=sys.stderr)
        return 0

    data = mp.mark_harvested(data, chapter, parsed.harvested_ids)
    data = mp.add_plants(
        data,
        chapter,
        [{"text": p.text, "kind": p.kind} for p in parsed.new_plants],
    )
    data = mp.expire_stale(data, chapter)
    data["updated_chapter"] = chapter
    mp.save_callbacks(data)

    n_open = len(mp.open_items(data))
    n_new = min(len(parsed.new_plants), mp.MAX_NEW_PER_CHAPTER)
    n_h = len(parsed.harvested_ids)
    print(
        f"micro-plants: ch{chapter:02d} +{n_new} plant(s), "
        f"{n_h} harvested, {n_open} open",
        file=sys.stderr,
    )
    return 0


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: extract_micro_plants.py <chapter> [--reextract]", file=sys.stderr)
        sys.exit(2)
    chapter = int(sys.argv[1])
    reextract = "--reextract" in sys.argv[2:]
    sys.exit(extract_for_chapter(chapter, reextract=reextract))


if __name__ == "__main__":
    main()
