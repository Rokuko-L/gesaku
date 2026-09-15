#!/usr/bin/env python3
"""
4-reader panel for full-arc novel evaluation.
Each reader has a distinct persona and evaluates the NOVEL, not chapters.
The disagreements between readers are where editorial decisions live.

Usage: python reader_panel.py
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm
from core import paths
from core.validation import ReaderPanelAnswers, parse_validated
import sys
import json
import re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from core.genre import load_genre
from core import llm

load_dotenv()

_HARDCODED_READERS = {
    "editor": {
        "name": "The Editor",
        "system": (
            "You are a senior fiction editor at a major publishing house. "
            "You've edited 200+ novels. You care about prose texture, subtext, "
            "sentence-level craft, and whether the voice is consistent and earned. "
            "You notice when the narrator over-explains, when dialogue sounds "
            "written rather than spoken, when a metaphor is borrowed rather than "
            "earned. You are not cruel but you are precise. You've seen enough "
            "competent prose to know the difference between good and alive. "
            "You respond with valid JSON only."
        ),
    },
    "genre_reader": {
        "name": "The Genre Reader",
        "system": load_genre()["evaluation"]["reader_panel"]["genre_reader_identity"],
    },
    "writer": {
        "name": "The Writer",
        "system": (
            "You are a published fantasy author with 5 novels and a Hugo nomination. "
            "You read as a craftsperson. You notice structure: where the beats fall, "
            "whether foreshadowing pays off, whether character arcs complete. You "
            "notice when technique shows versus when it disappears into the story. "
            "The highest compliment you give is 'I forgot I was reading.' The worst "
            "thing you can say is 'I can see the outline.' You care about the gap "
            "between what a novel attempts and what it achieves. You respond with "
            "valid JSON only."
        ),
    },
    "first_reader": {
        "name": "The First Reader",
        "system": (
            "You are a thoughtful general reader. Not a writer, not an editor, "
            "not a genre expert. You read for the experience. You know what you "
            "feel but not always why. You notice when you're moved, when you're "
            "bored, when you're confused, when you want to tell someone about "
            "what you just read. You don't use craft terminology. You say things "
            "like 'I didn't care about this part' and 'I had to put the book down "
            "after this scene because I needed a minute.' Your feedback is emotional "
            "and honest, not analytical. You respond with valid JSON only."
        ),
    },
}


def _load_readers():
    """Load reader personas from config if available, else use hardcoded defaults."""
    try:
        cfg = load_genre()
        readers_raw = (
            cfg
            .get("evaluation", {})
            .get("reader_panel", {})
            .get("readers", None)
        )
        if readers_raw and isinstance(readers_raw, list) and len(readers_raw) >= 4:
            result = {}
            for r in readers_raw[:4]:
                key = r.get("key", "")
                if key:
                    result[key] = {
                        "name": r.get("name", key),
                        "system": r.get("persona", ""),
                    }
            if len(result) >= 4:
                return result
    except Exception:
        pass
    return dict(_HARDCODED_READERS)


READERS = _load_readers()

def build_reader_prompt():
    cfg = load_genre()
    mods = cfg["evaluation"]["reader_panel"].get("prompt_modifications", {}) or {}
    earned_hint = mods.get("earned_ending_hint", "Does the ending feel earned by everything before it?")

    prompt = f"""You have just read a complete novel in summary form.
The summaries include chapter-by-chapter events, opening and closing passages
from each chapter, and key dialogue. The full novel is {{word_count:,}} words across
{{chapter_count}} chapters.

{{arc_summary}}

Now answer these questions about the NOVEL AS A WHOLE. Be specific.
Quote passages when you can. Name chapter numbers.

Respond with JSON:
{{{{
  "momentum_loss": "Where does the story lose momentum?...",

  "earned_ending": "{earned_hint}",

  "cut_candidate": "If the novel had to be 10% shorter (~{{cut_words:,}} words), which chapter or section would you cut first?...",

  "missing_scene": "Is there a scene the novel NEEDS that it doesn't have?...",

  "thinnest_character": "Which character feels thinnest?...",

  "best_scene": "What's the single best scene?...",

  "worst_scene": "What's the single weakest scene?...",

  "would_recommend": "Would you recommend this novel?...",

  "haunts_you": "Is there a line or moment that stays with you?...",

  "next_book": "Would you read the author's next book?..."
"""
    extra_qs = mods.get("extra_questions", {}) or {}
    for qkey, qtext in extra_qs.items():
        prompt += f'\n  "{qkey}": "{qtext}",'

    prompt += "\n}}"
    return prompt

def call_reader(reader_key, arc_summary):
    reader = READERS[reader_key]
    ch_files = sorted(paths.get_chapters_dir().glob("ch_*.md"))
    chapter_count = len(ch_files)
    word_count = sum(len(f.read_text(encoding="utf-8").split()) for f in ch_files) if ch_files else 0
    cut_words = int(word_count * 0.1) if word_count else 7000
    prompt = build_reader_prompt().format(arc_summary=arc_summary, word_count=word_count, chapter_count=chapter_count, cut_words=cut_words)
    raw = call_llm(prompt=prompt, system=reader["system"], model_key="judge", max_tokens=4000, timeout_role="standard", temperature=0.7)

    return parse_validated(
        ReaderPanelAnswers, raw, context=f"reader panel: {reader_key}"
    ).model_dump()

def find_disagreements(results):
    """Find where readers disagree -- that's where the editorial decisions live."""
    disagreements = []
    
    for question in ["momentum_loss", "cut_candidate", "thinnest_character", "worst_scene"]:
        answers = {k: str(v.get(question, "")) for k, v in results.items()}
        # Extract chapter numbers mentioned
        chapters_mentioned = {}
        for reader, answer in answers.items():
            chs = set(re.findall(r'Ch(?:apter)?\s*(\d+)', answer, re.IGNORECASE))
            chapters_mentioned[reader] = chs
        
        # Find chapters where only some readers flagged an issue
        all_chs = set()
        for chs in chapters_mentioned.values():
            all_chs.update(chs)
        
        for ch in all_chs:
            flagged_by = [r for r, chs in chapters_mentioned.items() if ch in chs]
            not_flagged = [r for r, chs in chapters_mentioned.items() if ch not in chs]
            if flagged_by and not_flagged:
                disagreements.append({
                    "question": question,
                    "chapter": int(ch),
                    "flagged_by": flagged_by,
                    "not_flagged": not_flagged,
                    "details": {r: answers[r][:200] for r in flagged_by}
                })
    
    return disagreements

def main():
    arc_summary = paths.get_arc_summary_path().read_text(encoding="utf-8")
    
    results = {}
    for reader_key, reader_info in READERS.items():
        print(f"\n{'='*50}")
        print(f"READING: {reader_info['name']}")
        print(f"{'='*50}")
        
        # Retry a failed reader once so a single truncated/errored response
        # doesn't silently drop that reader from the panel consensus.
        for rtry in range(1, 3):
            try:
                result = call_reader(reader_key, arc_summary)
                results[reader_key] = result
                
                # Print highlights
                print(f"  Momentum loss: {str(result.get('momentum_loss', ''))[:150]}...")
                print(f"  Best scene: {str(result.get('best_scene', ''))[:150]}...")
                print(f"  Would recommend: {str(result.get('would_recommend', ''))[:150]}...")
                break
            except Exception as e:
                print(f"  ERROR (attempt {rtry}/2): {e}")
                if rtry == 2:
                    print(f"  WARNING: reader '{reader_key}' failed twice — dropped from panel")
        else:
            continue
    
    # Find disagreements
    disagreements = find_disagreements(results)
    
    # Print consensus and disagreement
    print(f"\n{'='*60}")
    print("READER PANEL RESULTS")
    print(f"{'='*60}")
    
    for question in ["momentum_loss", "earned_ending", "cut_candidate", "missing_scene", 
                      "thinnest_character", "best_scene", "worst_scene", "would_recommend",
                      "haunts_you", "next_book"]:
        print(f"\n--- {question.upper()} ---")
        for reader_key in READERS:
            if reader_key in results:
                answer = str(results[reader_key].get(question, "N/A"))
                print(f"  [{READERS[reader_key]['name']}]: {answer[:300]}")
    
    if disagreements:
        print(f"\n{'='*60}")
        print("DISAGREEMENTS (editorial decisions needed)")
        print(f"{'='*60}")
        for d in disagreements:
            print(f"\n  {d['question']} -- Ch {d['chapter']}")
            print(f"    Flagged by: {', '.join(d['flagged_by'])}")
            print(f"    Not flagged: {', '.join(d['not_flagged'])}")
    
    # Save full results
    output = {
        "readers": results,
        "disagreements": disagreements,
        "timestamp": datetime.now().isoformat()
    }
    out_path = paths.get_edit_logs_dir() / "reader_panel.json"
    paths.save_json_atomic(output, out_path)
    print(f"\nSaved to {out_path}")

if __name__ == "__main__":
    main()
