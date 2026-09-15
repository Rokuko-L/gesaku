#!/usr/bin/env python3
"""
One-shot characters.md generator for foundation phase.
Reads seed.txt + voice.md + world.md + CRAFT.md, calls writer model.
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import TruncationError, call_llm, get_max_tokens_with_thinking
from core.paths import format_prompt
from core import outline
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from core.genre import load_genre
from core import paths

load_dotenv()

def call_writer(prompt, max_tokens=get_max_tokens_with_thinking(16000)):
    return call_llm(prompt=prompt, model_key="writer", max_tokens=max_tokens, timeout_role="xlong")

def main():
    seed_path = paths.get_seed_path()
    world_path = paths.get_world_path()
    voice_path = paths.get_voice_path()

    for name, p in [("seed.txt", seed_path), ("world.md", world_path), ("voice.md", voice_path)]:
        if not p.exists():
            print(f"ERROR: {name} not found at {p}", file=sys.stderr)
            sys.exit(1)

    seed = seed_path.read_text()
    world = world_path.read_text()
    voice = voice_path.read_text()

    voice_lines = voice.split('\n')
    part2_start = next(i for i, l in enumerate(voice_lines) if 'Part 2' in l)
    voice_part2 = '\n'.join(voice_lines[part2_start:])

    genre = load_genre()
    prompt = format_prompt(genre["generation"]["gen_characters_prompt"], seed=seed, world=world, voice_part2=voice_part2)

    print("Calling writer model...", file=sys.stderr)
    for attempt in range(2):
        try:
            result = call_writer(prompt)
        except TruncationError as e:
            print(f"  WARN: {e}, retrying...", file=sys.stderr)
            continue
        try:
            outline.validate_generator_output(result, "gen_characters.py", min_len=500, expected_headers=["# ", "## "])
            break
        except RuntimeError as e:
            if attempt == 0:
                print(f"  WARN: {e}, retrying...", file=sys.stderr)
            else:
                raise
    paths.get_characters_path().write_text(result, encoding="utf-8")
    print(result)

if __name__ == "__main__":
    main()