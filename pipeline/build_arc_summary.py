#!/usr/bin/env python3
"""
Build a condensed arc summary for full-novel evaluation.
For each chapter: first 150 words, last 150 words, plus any dialogue.
Gives the reader panel enough to evaluate the ARC without 72k tokens.
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import TruncationError, call_llm
from core.paths import get_novel_title
import hashlib
import json
import re
from pathlib import Path
from dotenv import load_dotenv
from core.genre import load_genre
from core import paths

load_dotenv()

# Cache of chapter-text-hash -> rendered arc entry. A 30-chapter run re-ran
# this pass 4 times (once per revision cycle plus export) for 120 calls over
# only 46 distinct chapter texts; 74 calls summarized a chapter that had not
# changed. Keyed on CONTENT, not mtime: `git_reset_hard` reverts file mtimes
# constantly in this pipeline, so an mtime key would miss on every re-run and
# quietly cost the whole saving.
ARC_CACHE_PATH = paths.get_arc_summary_cache_path


def _load_cache() -> dict:
    try:
        p = ARC_CACHE_PATH()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        ARC_CACHE_PATH().write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # a cache that cannot be written is a slowdown, never a failure

def call_writer(prompt, max_tokens=4000):
    return call_llm(prompt=prompt, system="You summarize novel chapters precisely. State what HAPPENS, what CHANGES, and what QUESTIONS are left open. No evaluation. No praise. Just events and shifts.", model_key="writer", max_tokens=max_tokens, timeout_role="short", temperature=0.1)

def extract_key_passages(text):
    """Get opening, closing, and best dialogue from a chapter."""
    words = text.split()
    opening = ' '.join(words[:150])
    closing = ' '.join(words[-150:])
    
    # Extract dialogue lines
    dialogue = re.findall(r'["""]([^"""]{20,})["""]', text)
    # Pick up to 3 longest dialogue lines
    dialogue.sort(key=len, reverse=True)
    top_dialogue = dialogue[:3]
    
    return opening, closing, top_dialogue

def process_chapter_arc_summary(path, ch, cache=None):
    text = path.read_text(encoding="utf-8")
    # Content hash, not mtime: this pipeline reverts mtimes constantly.
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if cache is not None and digest in cache:
        entry = cache[digest]
        print(f"Ch {ch}: cached ({len(text.split())}w)")
        return ch, entry
    wc = len(text.split())
    opening, closing, dialogue = extract_key_passages(text)
    
    # Get a 100-word summary from the model (retry once with a bigger budget on truncation)
    try:
        summary = call_writer(
            f"Summarize this chapter in exactly 3 sentences. What happens, what changes, what question is left open.\n\nCHAPTER {ch}:\n{text}",
            max_tokens=200
        )
    except TruncationError:
        print(f"Ch {ch}: summary truncated at 200 tokens — retrying with 600", file=sys.stderr)
        summary = call_writer(
            f"Summarize this chapter in exactly 3 sentences. What happens, what changes, what question is left open.\n\nCHAPTER {ch}:\n{text}",
            max_tokens=600
        )
    
    entry = f"""### Chapter {ch} ({wc} words)
**Summary:** {summary}

**Opening:** {opening}...

**Closing:** ...{closing}

**Key dialogue:**
"""
    for d in dialogue:
        entry += f'> "{d}"\n\n'
    
    print(f"Ch {ch}: summarized ({wc}w)")
    if cache is not None:
        cache[digest] = entry
    return ch, entry

def main():
    chapters_dir = paths.get_chapters_dir()
    chapter_files = sorted(chapters_dir.glob("ch_*.md"))
    if not chapter_files:
        print("No chapter files found!")
        return

    from concurrent.futures import ThreadPoolExecutor

    cache = _load_cache()
    before = len(cache)

    pairs = []
    for path in chapter_files:
        m = re.search(r"ch_(\d+)\.md", path.name)
        if not m:
            continue
        pairs.append((int(m.group(1)), path))

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [(ch, executor.submit(process_chapter_arc_summary, path, ch, cache))
                   for ch, path in pairs]

    results = []
    failed = []
    for ch, future in futures:
        try:
            results.append(future.result())
        except Exception as e:
            print(f"Error summarizing chapter {ch}: {e}", file=sys.stderr)
            failed.append(ch)

    # Retry the stragglers serially: these are transient LLM failures, and a
    # chapter absent from the arc means the reader panel judges a truncated
    # novel and targets rewrites from incomplete data.
    #
    # Retry, but do NOT abort: this stage is advisory input to the panel, and it
    # also runs during export (pipeline/phases/export.py) — killing a finished
    # novel because one summary failed would be far worse than an incomplete
    # arc, and the caller runs it under check=True.
    still_failed = []
    for ch in failed:
        entry = next((path for c, path in pairs if c == ch), None)
        recovered = None
        for attempt in range(2):
            if entry is None:
                break
            try:
                recovered = process_chapter_arc_summary(entry, ch, cache)
                break
            except Exception as e:
                print(f"  chapter {ch} retry {attempt + 1} failed: {e}",
                      file=sys.stderr)
        if recovered is None:
            still_failed.append(ch)
        else:
            results.append(recovered)

    if still_failed:
        print(f"WARNING: arc summary incomplete — chapter(s) {still_failed} "
              f"could not be summarized after retries; the reader panel will "
              f"judge a novel missing those chapters.", file=sys.stderr)
            
    # Sort results by chapter number
    results.sort(key=lambda x: x[0])
    summaries = [entry for _, entry in results]
    
    # Calculate total word count
    total_wc = sum(len(p.read_text(encoding="utf-8").split()) for p in chapter_files)
    num_chapters = len(chapter_files)
    
    # Assemble
    title = get_novel_title()
    full = f"""# {title.upper()}
## Full-Arc Summary for Reader Panel

This document contains chapter summaries, opening/closing passages,
and key dialogue for all {num_chapters} chapters. Total novel: {total_wc:,} words.

PREMISE: {load_genre()["generation"]["arc_summary_premise"]}

---

"""
    full += '\n---\n\n'.join(summaries)
    
    out_path = paths.get_arc_summary_path()
    out_path.write_text(full, encoding="utf-8")
    _save_cache(cache)
    reused = len(cache) - before
    print(f"\nSaved to {out_path} ({len(full.split())} words); "
          f"{reused} chapter summary(ies) reused from cache, "
          f"{len(cache)} cached total")

if __name__ == "__main__":
    main()
