#!/usr/bin/env python3
"""
Rebuild outline.md from the actual chapters.
Reads each chapter, calls the LLM for a structured summary,
and assembles into an outline that reflects the novel as-written.
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm, extract_text_from_response, get_max_tokens_with_thinking
from core import paths
from core import llm
import os
import sys
import json
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def parse_json(text):
    return llm.parse_json_response(text)


def call_model(prompt, max_tokens=1500):
    return call_llm(prompt=prompt, system="You produce structured outline entries for novel chapters. Be precise about what HAPPENS, what CHANGES, and what threads are planted/harvested. Output valid JSON only.", model_key="judge", max_tokens=max_tokens, temperature=0.1, timeout_role="short")

def process_chapter_outline(path, ch, text, wc, title_line):
    import time
    from core.llm import TruncationError

    prompt = f"""Analyze this chapter and produce a structured outline entry.

CHAPTER {ch}: "{title_line}" ({wc} words)

{text}

Return JSON with these fields:
- "title": the chapter title (string)
- "location": primary setting (string)
- "characters": list of characters who appear (list of strings)
- "summary": 2-3 sentence summary of what happens (string)
- "orientation_facts": list of orientation facts established/dramatized in the chapter (list of strings)
- "scene_stakes": one-sentence description of the concrete external stakes of the scene (string)
- "beats": list of 3-5 key story beats in order (list of strings)
- "try_fail": the try-fail cycle type: "yes-but", "no-and", "yes-and", or "no-but" (string)
- "plants": foreshadowing threads PLANTED in this chapter (list of strings)
- "harvests": foreshadowing threads PAID OFF in this chapter (list of strings)
- "emotional_arc": one sentence describing the emotional movement (string)
- "chapter_question": the question left open at chapter's end (string)

JSON only, no other text."""

    # Transient proxy/LLM failures (truncation, unparseable JSON) must not
    # nuke the whole export — retry each chapter a few times.
    data = None
    last_err = None
    for attempt in range(1, 4):
        try:
            raw_data = call_model(prompt)
            data = parse_json(raw_data)
            break
        except (TruncationError, ValueError, Exception) as e:
            last_err = e
            print(f"  [RETRY] Ch {ch} outline summary failed (attempt {attempt}/3): {e}", file=sys.stderr)
            time.sleep(3 * attempt)
    if data is None:
        raise last_err

    data["num"] = ch
    data["words"] = wc

    # Extract title cleanly from the first line of the file, bypassing LLM parsing inconsistency
    if ': ' in title_line:
        _, subtitle = title_line.split(': ', 1)
        data["title"] = subtitle.strip()
    else:
        data["title"] = title_line

    print(f"  {ch:2d}. {data['title']} ({wc}w)")
    return data
def main():
    # Load supporting docs for context
    characters = paths.get_characters_path().read_text(encoding="utf-8")[:3000]
    
    chapters_dir = paths.get_chapters_dir()
    chapter_files = sorted(chapters_dir.glob("ch_*.md"))
    if not chapter_files:
        print("No chapter files found!")
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    futures = []
    with ThreadPoolExecutor(max_workers=12) as executor:

        for path in chapter_files:
            m = re.search(r"ch_(\d+)\.md", path.name)
            if not m:
                continue
            ch = int(m.group(1))
            
            text = path.read_text(encoding="utf-8")
            wc = len(text.split())
            
            title_line = text.strip().split('\n')[0].lstrip('# ').strip()
            
            futures.append(executor.submit(process_chapter_outline, path, ch, text, wc, title_line))
            
    entries = []
    failures = []
    for future in as_completed(futures):
        try:
            entries.append(future.result())
        except Exception as e:
            failures.append(e)
            
    if failures:
        print(f"ERROR: {len(failures)} chapter(s) failed to summarize: {failures[:3]}", file=sys.stderr)
        print("FATAL: refusing to write an outline missing chapters — downstream eval/panel", file=sys.stderr)
        print("       would silently judge an incomplete book. Fix the failures and re-run.", file=sys.stderr)
        sys.exit(1)

    expected_nums = {int(re.search(r"ch_(\d+)\.md", p.name).group(1)) for p in chapter_files}
    got_nums = {e["num"] for e in entries}
    if got_nums != expected_nums:
        print(f"ERROR: outline entries {sorted(got_nums)} do not match chapter files {sorted(expected_nums)}", file=sys.stderr)
        sys.exit(1)

    # Sort entries by chapter number so outline is in order
    entries.sort(key=lambda x: x["num"])
    
    # Load existing outline header info
    try:
        old_outline = paths.get_outline_path().read_text(encoding="utf-8", errors="ignore")
    except Exception:
        old_outline = ""
    
    # Load dynamic title and cycle
    title = paths.get_novel_title()
    cycle_str = ""
    state_path = paths.get_state_path()
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            cycle = state.get("revision_cycle", 0)
            cycle_str = f", Cycle {cycle}"
        except Exception:
            pass

    # Build new outline
    lines = []
    lines.append(f"# {title.upper()}")
    lines.append("## Chapter Outline (reflects actual novel as-written)")
    lines.append("")
    lines.append(f"**{len(entries)} chapters, {sum(e['words'] for e in entries):,} words**")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    for e in entries:
        lines.append(f"### Ch {e['num']}: {e['title']}")
        lines.append(f"**{e['words']} words** | **Location:** {e.get('location', 'N/A')}")
        lines.append(f"- **Characters:** {', '.join(e.get('characters', []))}")
        lines.append(f"- **Try-fail cycle:** {e.get('try_fail', 'N/A')}")
        lines.append(f"- **Emotional arc:** {e.get('emotional_arc', 'N/A')}")
        lines.append(f"- **Orientation Facts:**")
        for f in e.get("orientation_facts", []):
            lines.append(f"  - {f}")
        lines.append(f"- **Scene Stakes:** {e.get('scene_stakes', 'N/A')}")
        lines.append("")
        lines.append(f"**Summary:** {e.get('summary', 'N/A')}")
        lines.append("")
        lines.append("**Beats:**")
        for b in e.get("beats", []):
            lines.append(f"1. {b}")
        lines.append("")
        if e.get("plants"):
            lines.append("**Plants:**")
            for p in e["plants"]:
                lines.append(f"- {p}")
            lines.append("")
        if e.get("harvests"):
            lines.append("**Harvests:**")
            for h in e["harvests"]:
                lines.append(f"- {h}")
            lines.append("")
        lines.append(f"**Chapter question:** {e.get('chapter_question', 'N/A')}")
        lines.append("")
        lines.append("---")
        lines.append("")
    
    # Foreshadowing ledger — cluster free-text plants/harvests by token overlap
    # so near-duplicate wording (the common case from per-chapter LLM dumps)
    # collapses into one thread instead of the old exact p[:60] key.
    lines.append("## FORESHADOWING LEDGER")
    lines.append("")

    from core.micro_plants import match_plant_harvest_threads

    plant_rows, harvest_rows = [], []
    for e in entries:
        for p in e.get("plants") or []:
            plant_rows.append({"text": str(p), "chapter": e["num"]})
        for h in e.get("harvests") or []:
            harvest_rows.append({"text": str(h), "chapter": e["num"]})
    threads = match_plant_harvest_threads(plant_rows, harvest_rows)

    paid = sum(1 for t in threads if t["status"] == "paid off")
    open_n = len(threads) - paid
    lines.append(f"*{len(threads)} clustered threads · {paid} paid · {open_n} open*")
    lines.append("")
    lines.append("| Thread | Planted | Harvested | Status |")
    lines.append("|--------|---------|-----------|--------|")
    for t in threads:
        planted = f"Ch {t['planted']}" if t.get("planted") else ""
        harvested = f"Ch {t['harvest']}" if t.get("harvest") else ""
        status = t["status"]
        lines.append(f"| {t['thread']} | {planted} | {harvested} | {status} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*Outline rebuilt from actual chapters{cycle_str}. "
                 "Ledger clustered by plant↔harvest token overlap.*")
    
    out = '\n'.join(lines)
    paths.get_outline_path().write_text(out, encoding="utf-8")
    print(f"\nSaved outline.md ({len(out.split())} words)")

if __name__ == "__main__":
    main()

