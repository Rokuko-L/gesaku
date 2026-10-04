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
from core.validation import ChapterOutlineEntry, parse_validated
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


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
            data = parse_validated(
                ChapterOutlineEntry, raw_data, context=f"Ch {ch} outline summary"
            ).model_dump()
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
def _harvest_text_and_source(h) -> tuple[str, int | None]:
    """A harvest is `{"thread", "declared_chapter"}` after attribution, or a bare string."""
    if isinstance(h, dict):
        return str(h.get("thread") or ""), h.get("declared_chapter")
    return str(h), None


def attribute_harvests(ch: int, harvests, prior_plants, attempts: int = 2) -> list:
    """For each of this chapter's payoffs, ask which earlier plant it resolves.

    The chapter summarizer runs in isolation, so it can only ever describe a
    payoff in that chapter's own vocabulary — which is why pairing by token
    overlap left 101 of v4's 116 harvests with no plant. This second, small pass
    shows the model the plants declared in earlier chapters and lets it *name*
    the one it resolves.

    Fail-soft: an unusable answer leaves the payoffs unattributed (they fall
    back to inference) rather than failing the export.
    """
    if not harvests or not prior_plants:
        return [None] * len(harvests)

    from core.validation import HarvestAttributions

    listed_payoffs = "\n".join(f"{i}. {h}" for i, h in enumerate(harvests, 1))
    listed_plants = "\n".join(
        f"- ch{p['chapter']}: {p['text']}" for p in prior_plants
    )
    prompt = paths.load_prompt("attribute_harvests").format(
        ch=ch, listed_payoffs=listed_payoffs, listed_plants=listed_plants
    )

    out = [None] * len(harvests)
    valid_chapters = {p["chapter"] for p in prior_plants}
    for attempt in range(1, attempts + 1):
        try:
            parsed = parse_validated(
                HarvestAttributions, call_model(prompt, max_tokens=800),
                context=f"Ch {ch} harvest attribution",
            )
        except Exception as e:  # noqa: BLE001 — any failure degrades to inference
            print(f"  [RETRY] Ch {ch} attribution failed (attempt {attempt}/{attempts}): {e}",
                  file=sys.stderr)
            continue
        for a in parsed.attributions:
            i = a.index - 1
            pc = a.planted_chapter
            # Only a chapter we actually showed the model. A payoff cannot
            # resolve its own or a later chapter, and a hallucinated number must
            # not be written into the outline as a declared source.
            if (0 <= i < len(harvests) and pc is not None
                    and int(pc) in valid_chapters and int(pc) < ch):
                out[i] = int(pc)
        return out
    return out


def attribute_entries(entries: list) -> None:
    """Resolve each payoff to the earlier chapter whose plant it pays off.

    Runs after summarization so it can see every chapter's plants at once, and
    in parallel because each call is small. Mutates entries in place: each
    harvest becomes {"thread": str, "declared_chapter": int|None}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    by_chapter = {e["num"]: [str(p) for p in e.get("plants") or []] for e in entries}

    def prior_plants_for(ch: int) -> list:
        return [{"chapter": n, "text": t}
                for n in sorted(by_chapter) if n < ch
                for t in by_chapter[n]]

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for e in entries:
            harvests = [str(h) for h in e.get("harvests") or []]
            futures[executor.submit(
                attribute_harvests, e["num"], harvests, prior_plants_for(e["num"])
            )] = (e, harvests)
        for future in as_completed(futures):
            entry, harvests = futures[future]
            try:
                attributions = list(future.result())
            except Exception as e:  # noqa: BLE001 — attribution is best-effort
                print(f"  [WARN] Ch {entry['num']} attribution unavailable: {e}",
                      file=sys.stderr)
                attributions = [None] * len(harvests)
            if len(attributions) < len(harvests):
                attributions += [None] * (len(harvests) - len(attributions))
            entry["harvests"] = [
                {"thread": text, "declared_chapter": pc}
                for text, pc in zip(harvests, attributions)
            ]


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

    attribute_entries(entries)

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
                text, decl = _harvest_text_and_source(h)
                # The declared source rides in the bullet so the console can
                # read identity straight off the file instead of re-inferring it.
                lines.append(f"- {f'[payoff of ch{decl}] ' if decl else ''}{text}")
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
            text, decl = _harvest_text_and_source(h)
            harvest_rows.append({"text": text, "chapter": e["num"],
                                 "declared_chapter": decl})
    threads = match_plant_harvest_threads(plant_rows, harvest_rows)

    paid = sum(1 for t in threads if t["status"] == "paid off")
    declared = sum(1 for t in threads if t.get("match") == "declared")
    recalled_n = sum(1 for t in threads if t["status"] == "recalled")
    open_n = sum(1 for t in threads if t["status"] == "open")
    # Say what each number means: the old header folded plant-less payoffs into
    # "open", which made the ledger read as loosely closed as it could.
    lines.append(f"*{len(threads)} threads · {paid} paid ({declared} declared, "
                 f"{paid - declared} inferred) · {recalled_n} payoff without a setup "
                 f"· {open_n} setup without a payoff*")
    lines.append("")
    lines.append("| Thread | Planted | Harvested | Status |")
    lines.append("|--------|---------|-----------|--------|")
    for t in threads:
        planted = f"Ch {t['planted']}" if t.get("planted") else ""
        harvested = f"Ch {t['harvest']}" if t.get("harvest") else ""
        status = t["status"]
        if status == "paid off" and t.get("match") == "declared":
            status = "paid off (declared)"
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

