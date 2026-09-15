#!/usr/bin/env python3
"""
Revision chapter generator. Rewrites a chapter from a specific revision brief.
Usage: python gen_revision.py <chapter_num> <brief_file>
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm
from core.paths import get_novel_title
from core import outline
from core import textstats
import sys
from pathlib import Path
from dotenv import load_dotenv
from core.genre import load_genre, prose_mode_system_block
from core import paths

load_dotenv()

def call_writer(prompt, max_tokens=16000):
    genre_cfg = load_genre()
    system = genre_cfg["identity"]["revision_system"]
    perspective = genre_cfg.get("perspective", "")
    if perspective:
        if perspective == "first_person":
            system += ("\n\nMANDATORY PERSPECTIVE: Keep the chapter in STRICT FIRST-PERSON "
                       "limited narration from the POV character ('I/me/my'). No third-person narration.")
        else:
            system += ("\n\nMANDATORY PERSPECTIVE: Keep the chapter in STRICT THIRD-PERSON "
                       "limited narration anchored to the POV character ('he/she/they' or the "
                       "character's name). Never switch to first-person narration.")
    system += prose_mode_system_block(genre_cfg)
    return call_llm(prompt=prompt, system=system, model_key="writer", max_tokens=max_tokens, beta_context=True, timeout_role="standard", temperature=0.8, raise_on_truncation=True)

def main():
    ch_num = int(sys.argv[1])
    brief_file = sys.argv[2]
    
    voice = paths.get_voice_path().read_text(encoding="utf-8")
    characters = paths.get_characters_path().read_text(encoding="utf-8")
    world = paths.get_world_path().read_text(encoding="utf-8")
    brief = Path(brief_file).read_text(encoding="utf-8")
    
    # Load adjacent chapters for continuity (sentence-boundary trimmed)
    chapters_dir = paths.get_chapters_dir()
    prev_path = chapters_dir / f"ch_{ch_num - 1:02d}.md"
    next_path = chapters_dir / f"ch_{ch_num + 1:02d}.md"
    prev_tail = textstats.tail_context(prev_path.read_text(encoding="utf-8"), max_words=600) if prev_path.exists() else "(first chapter)"
    next_head = textstats.head_context(next_path.read_text(encoding="utf-8"), max_words=300) if next_path.exists() else "(last chapter)"
    
    # Load old version if exists
    old_path = chapters_dir / f"ch_{ch_num:02d}.md"
    old_text = old_path.read_text(encoding="utf-8") if old_path.exists() else "(no existing draft)"

    title = get_novel_title()

    # Soft optional callbacks from prose-emergent plants (never a MUST).
    try:
        from core import micro_plants as mp
        callback_block = mp.soft_inject_block(mp.load_callbacks(), ch_num)
    except Exception:
        callback_block = ""

    # Pull the latest eval's AI-pattern findings so the revision removes them
    ai_feedback = ""
    try:
        eval_dir = paths.get_eval_logs_dir()
        candidates = sorted(
            (p for p in eval_dir.glob(f"*_ch{ch_num:02d}.json")),
            key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            import json
            ev = json.loads(candidates[0].read_text(encoding="utf-8"))
            bits = []
            pats = ev.get("ai_patterns_detected") or []
            revs = ev.get("top_3_revisions") or []
            tics = (ev.get("slop") or {}).get("prose_tics") or []
            if pats:
                bits.append("AI PATTERNS DETECTED (eliminate these):\n" +
                            "\n".join(f"  - {p}" for p in pats))
            if revs:
                bits.append("PRIORITY REVISIONS:\n" +
                            "\n".join(f"  - {r}" for r in revs))
            if tics:
                bits.append("MECHANICAL TICS (rewrite these constructions):\n" +
                            "\n".join(f"  - {t['tic']}: {t['count']}x" for t in tics))
            if bits:
                ai_feedback = "\n\nEVALUATOR FEEDBACK (address every point in your rewrite):\n" + "\n\n".join(bits)
    except Exception:
        pass

    prompt = f"""Rewrite Chapter {ch_num} of "{title}."

REVISION BRIEF (follow this exactly):
{brief}

VOICE DEFINITION:
{voice}

CHARACTER REGISTRY:
{characters}

WORLD BIBLE:
{world}

PREVIOUS CHAPTER ENDING (maintain continuity):
{prev_tail}

NEXT CHAPTER OPENING (end so this flows into it):
{next_head}

THE EXISTING DRAFT (use as raw material -- keep what works, cut what doesn't):
{old_text}

ANTI-PATTERN RULES:
{load_genre()["generation"]["anti_pattern_rules"]}
{callback_block}
{ai_feedback}

FORMATTING:
Start the chapter with a single markdown H1 title line, exactly:
`# Chapter {ch_num}: <Chapter Title>`
Nothing else on that line — no bold, no "##", no slug/codename. Keep the existing
title unless the brief changes it.
Write the FULL revised chapter now."""

    print(f"Rewriting Chapter {ch_num}...", file=sys.stderr)
    result = call_writer(prompt)
    
    out_path = chapters_dir / f"ch_{ch_num:02d}.md"
    out_path.write_text(outline.normalize_chapter_heading(result, ch_num), encoding="utf-8")
    print(f"Saved to {out_path}", file=sys.stderr)
    print(f"Word count: {len(result.split())}", file=sys.stderr)

if __name__ == "__main__":
    main()
