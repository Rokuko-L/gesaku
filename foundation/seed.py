#!/usr/bin/env python3
"""
seed.py -- Generate fantasy novel seed concepts.

Usage:
  uv run python seed.py              # Generate 10 concepts, pick one
  uv run python seed.py --count=5    # Generate 5 concepts
  uv run python seed.py --riff "magic costs memories"  # Riff on an idea
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm
import argparse
import json
from pathlib import Path
from dotenv import load_dotenv
from core.genre import load_genre

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def call_writer(prompt, max_tokens=4000):
    return call_llm(prompt=prompt, system=load_genre()["identity"]["seed_system"], model_key="writer", max_tokens=max_tokens, temperature=1.0, beta_context=True, timeout_role="short")


GENERATE_PROMPT = load_genre()["generation"]["seed_generate_prompt"]

RIFF_PROMPT = load_genre()["generation"]["seed_riff_prompt"]


def main():
    parser = argparse.ArgumentParser(description="Generate novel seed concepts")
    parser.add_argument("--count", type=int, default=10,
                        help="Number of concepts to generate (default: 10)")
    parser.add_argument("--riff", type=str, default=None,
                        help="Riff on an existing idea")
    args = parser.parse_args()

    if args.riff:
        print(f"Riffing on: {args.riff}\n")
        prompt = RIFF_PROMPT.format(idea=args.riff)
    else:
        print(f"Generating {args.count} seed concepts...\n")
        prompt = GENERATE_PROMPT.format(count=args.count)

    result = call_writer(prompt, max_tokens=8000)
    print(result)
    print("\n" + "=" * 60)
    print("To pick a seed, copy the concept you like into seed.txt:")
    print("  nano seed.txt")
    print("Or remix several concepts into your own seed.")
    print("Then proceed to Step 2 in Docs/workflow.md.")


if __name__ == "__main__":
    main()
