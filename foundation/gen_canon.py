#!/usr/bin/env python3
"""
Generate canon.md by extracting all hard facts from world.md + characters.md.
Facts are tagged visible_from=N so drafting can withhold sealed truths.
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import TruncationError, call_llm, get_max_tokens_with_thinking
from core import paths
from core import canon as canon_mod
from core import outline
import json
import sys
from dotenv import load_dotenv
from core.genre import load_genre, chapters_total as genre_chapters_total

load_dotenv()

def call_writer(prompt, max_tokens=get_max_tokens_with_thinking(16000)):
    return call_llm(prompt=prompt, model_key="writer", max_tokens=max_tokens, timeout_role="xlong")

FOUNDATION_CANON_PROMPT = paths.load_prompt("foundation_canon")


def _total_chapters() -> int:
    """Chapter count — genre config owns it, state mirrors it."""
    total = genre_chapters_total()
    if total:
        return total
    try:
        state = json.loads(paths.get_state_path().read_text(encoding="utf-8"))
        return int(state.get("chapters_total") or 0) or 24
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 24


def normalize_foundation_bullets(result: str) -> str:
    """Ensure every bullet has a visible_from tag (default 1)."""
    out_lines = []
    for line in result.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("-", "*")):
            # Drop headers/preamble; keep blank lines between bullets.
            if stripped.startswith("#"):
                continue
            if not stripped:
                out_lines.append("")
            continue
        if canon_mod.VISIBLE_FROM_RE.match(line):
            out_lines.append(stripped)
        else:
            text = canon_mod.BARE_BULLET_RE.match(stripped)
            body = text.group(1) if text else stripped.lstrip("-* ")
            out_lines.append(f"- visible_from=1: {body}")
    return "\n".join(out_lines).strip() + "\n"


def main():
    required = {
        "world.md": paths.get_world_path(),
        "characters.md": paths.get_characters_path(),
        "seed.txt": paths.get_seed_path(),
    }
    for name, p in required.items():
        if not p.exists():
            print(f"ERROR: {name} not found at {p}", file=sys.stderr)
            sys.exit(1)

    world = required["world.md"].read_text(encoding="utf-8")
    characters = required["characters.md"].read_text(encoding="utf-8")
    seed = required["seed.txt"].read_text(encoding="utf-8")
    total_chapters = _total_chapters()

    prompt = FOUNDATION_CANON_PROMPT.format(
        seed=seed, world=world, characters=characters, total_chapters=total_chapters
    )

    print("Calling writer model...", file=sys.stderr)
    result = ""
    for attempt in range(2):
        try:
            result = call_writer(prompt)
        except TruncationError as e:
            print(f"  WARN: {e}, retrying...", file=sys.stderr)
            continue
        try:
            outline.validate_generator_output(result, "gen_canon.py", min_len=100, expected_headers=None)
            result = normalize_foundation_bullets(result)
            parsed = canon_mod.parse_canon(
                "## Foundation (background truth, not yet revealed to readers)\n\n" + result
            )
            if not parsed.foundation_facts:
                raise RuntimeError("no parseable foundation facts (visible_from bullets required)")
            if parsed.malformed_visible_from:
                bad = "; ".join(parsed.malformed_visible_from[:5])
                raise RuntimeError(
                    "malformed visible_from tag(s) — fail closed, retry: " + bad
                )
            break
        except RuntimeError as e:
            if attempt == 0:
                print(f"  WARN: {e}, retrying...", file=sys.stderr)
            else:
                raise

    result = "## Foundation (background truth, not yet revealed to readers)\n\n" + result
    parsed = canon_mod.parse_canon(result)
    sealed = parsed.sealed_facts()
    reveal = parsed.reveal_chapter()
    paths.get_canon_path().write_text(result, encoding="utf-8")
    print(result)
    print(
        f"Canon: {len(parsed.foundation_facts)} facts "
        f"({len(sealed)} sealed, reveal_chapter={reveal})",
        file=sys.stderr,
    )

if __name__ == "__main__":
    main()
