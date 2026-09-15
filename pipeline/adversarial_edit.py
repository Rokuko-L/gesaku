#!/usr/bin/env python3
"""
Adversarial editing pass: ask the judge to CUT 500 words from each chapter.
What gets cut reveals what's weakest. The cut list IS the revision plan.

Usage: python adversarial_edit.py 1        # single chapter
       python adversarial_edit.py all      # all chapters
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm, extract_text_from_response, get_max_tokens_with_thinking
from core import paths
from core import llm
from core import canon as canon_mod
import os
import sys
import json
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def call_judge(prompt, max_tokens=8000):
    return call_llm(prompt=prompt, system="You are a ruthless literary editor. You cut fat from prose. You have no sentiment about good-enough sentences -- if a sentence isn't earning its place, it goes. You quote exactly from the text. You never invent or paraphrase. Always respond with valid JSON.", model_key="judge", max_tokens=max_tokens, temperature=0.3, timeout=300)

def parse_json(text):
    return llm.parse_json_response(text)

EDIT_PROMPT = paths.load_prompt("adversarial_edit")

def edit_chapter(ch_num):
    chapters_dir = paths.get_chapters_dir()
    edit_log_dir = paths.get_edit_logs_dir()
    ch_path = chapters_dir / f"ch_{ch_num:02d}.md"
    text = ch_path.read_text(encoding="utf-8")
    word_count = len(text.split())

    # Load prior-chapter disclosure only (no sealed foundation, no future as-of)
    canon_text = ""
    canon_path = paths.get_canon_path()
    if canon_path.exists():
        raw = canon_path.read_text(encoding="utf-8")
        canon_text = canon_mod.disclosure_md(canon_mod.parse_canon(raw), ch_num)

    prompt = EDIT_PROMPT.format(chapter_text=text, word_count=word_count, canon_context=canon_text or "(first chapter or no canon established yet)")
    raw = call_judge(prompt)
    try:
        result = parse_json(raw)
    except Exception as e:
        print("RAW RESPONSE FROM JUDGE:")
        print(raw)
        raise e
    
    # Save log
    log_path = edit_log_dir / f"ch{ch_num:02d}_cuts.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    # Validate LLM quotes match chapter text
    cuts = result.get("cuts", [])
    fat_pct = result.get("overall_fat_percentage", 0)
    print(f"  [DEBUG] Chapter {ch_num:02d} fat score generated: {fat_pct}%", file=sys.stderr)
    
    matched_count = 0
    failed_cuts = []
    for cut in cuts:
        quote = cut.get("quote", "")
        if not quote.strip():
            continue
        # Normalize whitespace to check presence
        norm_quote = re.sub(r"\s+", " ", quote).strip()
        norm_text = re.sub(r"\s+", " ", text).strip()
        if norm_quote in norm_text:
            matched_count += 1
        else:
            failed_cuts.append(quote)
            
    print(f"  [DEBUG] Quote Match Validation: {matched_count}/{len(cuts)} quotes matched in chapter text.", file=sys.stderr)
    if failed_cuts:
        print(f"  [DEBUG][WARN] {len(failed_cuts)} recommended cuts do NOT match the chapter text exactly (LLM alignment issue).", file=sys.stderr)
        for i, fq in enumerate(failed_cuts[:3]):
            print(f"    - Misaligned quote {i+1}: {fq[:60]}...", file=sys.stderr)
    
    return result, word_count

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Adversarial edit a chapter or all chapters")
    parser.add_argument("chapter", help="Chapter number (integer) or 'all'")
    parser.add_argument("--project", default=None, help="Project name (under projects/)")
    args = parser.parse_args()

    if args.project:
        paths.set_project_name(args.project)

    if args.chapter == "all":
        chapters = sorted([int(m.group(1)) for p in paths.get_chapters_dir().glob("ch_*.md") if (m := re.match(r"ch_(\d+)\.md", p.name))])
    else:
        try:
            chapters = [int(args.chapter)]
        except ValueError:
            print("Error: chapter must be an integer or 'all'")
            sys.exit(1)
    
    for ch in chapters:
        print(f"\n{'='*50}")
        print(f"EDITING CH {ch}")
        print(f"{'='*50}")
        
        try:
            result, wc = edit_chapter(ch)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue
        
        cuts = result.get("cuts", [])
        cuttable = result.get("total_cuttable_words", 0)
        fat_pct = result.get("overall_fat_percentage", 0)
        verdict = result.get("one_sentence_verdict", "")
        
        # Count by type
        type_counts = {}
        for c in cuts:
            t = c.get("type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
        
        print(f"  Words: {wc}")
        print(f"  Cuts found: {len(cuts)}")
        print(f"  Cuttable words: ~{cuttable} ({fat_pct}% fat)")
        print(f"  By type: {type_counts}")
        print(f"  Verdict: {verdict}")
        print(f"  Tightest: {result.get('tightest_passage', '')[:100]}...")
        print(f"  Loosest:  {result.get('loosest_passage', '')[:100]}...")

if __name__ == "__main__":
    main()
