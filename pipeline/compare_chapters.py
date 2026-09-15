#!/usr/bin/env python3
"""
Comparative ranking: pair chapters head-to-head.
The judge picks a winner and quotes the deciding moments.
Produces a true rank order from round-robin tournament.

Usage: python compare_chapters.py          # full tournament
       python compare_chapters.py 1 10     # single matchup
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm, extract_text_from_response, get_max_tokens_with_thinking
from core import paths
import os
import sys
import json
import re
import random
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from core import validation
from core import llm

load_dotenv()

def call_judge(prompt, max_tokens=4000):
    return call_llm(prompt=prompt, system="You are a literary editor comparing two chapters of the same novel. You pick the better one. You are not allowed to call it a tie. You quote specific passages to justify your choice. Respond with valid JSON only.", model_key="judge", max_tokens=max_tokens, temperature=0.2, timeout_role="standard")

def parse_json(text):
    return llm.parse_json_response(text)

COMPARE_PROMPT = paths.load_prompt("compare_chapters")

def compare(ch_a, ch_b):
    chapters_dir = paths.get_chapters_dir()
    text_a = (chapters_dir / f"ch_{ch_a:02d}.md").read_text()
    text_b = (chapters_dir / f"ch_{ch_b:02d}.md").read_text()
    
    # Truncate to ~3000 words each to fit context
    words_a = text_a.split()
    words_b = text_b.split()
    if len(words_a) > 3000:
        text_a = ' '.join(words_a[:3000]) + "\n[truncated]"
    if len(words_b) > 3000:
        text_b = ' '.join(words_b[:3000]) + "\n[truncated]"
    
    prompt = COMPARE_PROMPT.format(
        ch_a=ch_a, ch_b=ch_b,
        text_a=text_a, text_b=text_b
    )
    raw = call_judge(prompt)
    result = validation.parse_validated(
        validation.CompareOutput, raw, context=f"Ch {ch_a} vs Ch {ch_b} verdict"
    ).model_dump()
    result["ch_a"] = ch_a
    result["ch_b"] = ch_b
    return result

def run_tournament(chapters):
    """Swiss-style tournament: pair by similar Elo, run enough rounds to rank."""
    # Initialize Elo ratings
    elo = {ch: 1500 for ch in chapters}
    K = 32
    matchups = []
    
    # Run 3-4 rounds of Swiss pairings
    n_rounds = 4
    for round_num in range(n_rounds):
        # Sort by Elo, pair adjacent
        ranked = sorted(chapters, key=lambda c: elo[c], reverse=True)
        pairs = []
        used = set()
        for i in range(0, len(ranked) - 1, 2):
            a, b = ranked[i], ranked[i+1]
            if (a, b) not in used and (b, a) not in used:
                pairs.append((a, b))
                used.add((a, b))
        
        print(f"\n--- Round {round_num + 1} ({len(pairs)} matchups) ---")
        for ch_a, ch_b in pairs:
            try:
                result = compare(ch_a, ch_b)
                winner = result.get("winner_chapter", result.get("winner"))
                margin = result.get("margin", "?")
                
                # Handle "A"/"B" vs chapter number
                if winner == "A":
                    winner = ch_a
                elif winner == "B":
                    winner = ch_b
                else:
                    winner = int(winner)
                
                loser = ch_b if winner == ch_a else ch_a
                
                # Update Elo
                exp_a = 1 / (1 + 10 ** ((elo[ch_b] - elo[ch_a]) / 400))
                score_a = 1.0 if winner == ch_a else 0.0
                elo[ch_a] += K * (score_a - exp_a)
                elo[ch_b] += K * ((1 - score_a) - (1 - exp_a))
                
                result["winner_resolved"] = winner
                matchups.append(result)
                
                print(f"  Ch {ch_a} vs Ch {ch_b}: winner=Ch {winner} ({margin})")
                
            except Exception as e:
                print(f"  Ch {ch_a} vs Ch {ch_b}: ERROR ({e})")
    
    # Final ranking
    ranking = sorted(chapters, key=lambda c: elo[c], reverse=True)
    
    return ranking, elo, matchups

def main():
    if len(sys.argv) == 3:
        # Single matchup
        ch_a, ch_b = int(sys.argv[1]), int(sys.argv[2])
        result = compare(ch_a, ch_b)
        print(json.dumps(result, indent=2))
    else:
        # Full tournament
        chapters = sorted([int(m.group(1)) for p in paths.get_chapters_dir().glob("ch_*.md") if (m := re.match(r"ch_(\d+)\.md", p.name))])
        ranking, elo, matchups = run_tournament(chapters)
        
        print(f"\n{'='*50}")
        print("FINAL RANKING")
        print(f"{'='*50}")
        for i, ch in enumerate(ranking):
            print(f"  {i+1:2d}. Ch {ch:2d}  (Elo: {elo[ch]:.0f})")
        
        # Save results
        results = {
            "ranking": ranking,
            "elo": {str(k): round(v) for k, v in elo.items()},
            "matchups": matchups,
            "timestamp": datetime.now().isoformat()
        }
        out_path = paths.get_edit_logs_dir() / "tournament_results.json"
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nSaved to {out_path}")

if __name__ == "__main__":
    main()
