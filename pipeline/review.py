#!/usr/bin/env python3
"""
Deep manuscript review via Opus.

Sends the full novel to Claude Opus for dual-persona review:
  1. Literary critic (newspaper book review style)
  2. Professor of fiction (specific, actionable craft suggestions)

Usage:
  python review.py                    # Review, save to edit_logs/
  python review.py --output reviews.md  # Also save human-readable copy
  python review.py --parse            # Parse last review into actionable items
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm, extract_text_from_response, get_max_tokens_with_thinking, TruncationError
from core import paths
import os
import sys
import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Use Opus for reviews — it's the best at literary analysis

REVIEW_PROMPT = paths.load_prompt("review_manuscript")


def call_opus(prompt, max_tokens=32000):
    """Call Opus with the full manuscript.

    A full-manuscript review easily exceeds 16k tokens (the 83k-word v4
    manuscript truncated at ~16k words). Raise the cap and treat a
    residual truncation as a soft warning — a partial review still
    contains usable craft notes.
    """
    print(f"Sending to Opus ({len(prompt):,} chars)...", file=sys.stderr)
    try:
        return call_llm(
            prompt=prompt, model_key="review", max_tokens=max_tokens,
            beta_context=True, timeout_role="long", raise_on_truncation=True,
        )
    except TruncationError as e:
        print(f"  WARN: review truncated ({e}); using partial review.", file=sys.stderr)
        return call_llm(
            prompt=prompt, model_key="review", max_tokens=max_tokens,
            beta_context=True, timeout_role="long", raise_on_truncation=False,
        )


def get_title():
    """Extract novel title from first chapter or outline."""
    outline = paths.get_outline_path()
    if outline.exists():
        first_line = outline.read_text().split("\n")[0]
        title = first_line.lstrip("# ").strip()
        if title:
            return title
    ch1 = paths.get_chapters_dir() / "ch_01.md"
    if ch1.exists():
        first_line = ch1.read_text().split("\n")[0]
        return first_line.lstrip("# ").strip()
    return "Untitled Novel"


def build_manuscript():
    """Concatenate all chapters into a single text."""
    chapters = sorted(paths.get_chapters_dir().glob("ch_*.md"))
    if not chapters:
        print("ERROR: No chapters found.", file=sys.stderr)
        sys.exit(1)
    
    parts = []
    for ch in chapters:
        parts.append(ch.read_text())
    
    manuscript = "\n\n---\n\n".join(parts)
    wc = len(manuscript.split())
    print(f"Manuscript: {len(chapters)} chapters, {wc:,} words", file=sys.stderr)
    return manuscript


def parse_review(review_text):
    """Parse a review into structured actionable items."""
    items = []
    
    # Split into critic and professor sections
    sections = re.split(r'(?:Professor|PROFESSOR|professor).*?(?:Review|Assessment|Analysis|Craft)', 
                        review_text, maxsplit=1)
    
    critic_text = sections[0] if sections else review_text
    professor_text = sections[1] if len(sections) > 1 else ""
    
    # Extract star rating
    star_match = re.search(r'★+½?|(\d+\.?\d*)\s*/?\s*(?:out of\s*)?(?:five|5)', critic_text)
    stars = None
    if star_match:
        star_str = star_match.group(0)
        stars = star_str.count('★') + (0.5 if '½' in star_str else 0)
    
    # Extract professor's numbered items
    # Look for patterns like "1. Title" or "1. **Title**" or "Problem:" or "Suggestion:"
    prof_items = re.split(r'\n(?=\d+\.\s)', professor_text)
    
    for section in prof_items:
        if not section.strip():
            continue
        
        # Extract the item title/number
        title_match = re.match(r'(\d+)\.\s+(.+?)(?:\n|$)', section)
        if not title_match:
            continue
        
        num = int(title_match.group(1))
        title = re.sub(r'\*\*(.+?)\*\*', r'\1', title_match.group(2)).strip()
        
        # Classify severity based on language
        text_lower = section.lower()
        if any(w in text_lower for w in ['major', 'significant', 'primary', 'most important']):
            severity = "major"
        elif any(w in text_lower for w in ['minor', 'small', 'slight', 'cosmetic']):
            severity = "minor"
        else:
            severity = "moderate"
        
        # Classify type
        if any(w in text_lower for w in ['cut', 'compress', 'trim', 'reduce', 'consolidate']):
            fix_type = "compression"
        elif any(w in text_lower for w in ['add', 'expand', 'introduce', 'give', 'more']):
            fix_type = "addition"  
        elif any(w in text_lower for w in ['repetit', 'recurring', 'frequency', 'tic', 'gesture']):
            fix_type = "mechanical"
        elif any(w in text_lower for w in ['restructur', 'rearrang', 'move', 'reorganiz']):
            fix_type = "structural"
        else:
            fix_type = "revision"
        
        # Check if this is qualified/hedged (diminishing returns signal)
        qualified = any(phrase in text_lower for phrase in [
            'individually fine', 'largely successful', 'each instance works',
            'minor relative to', 'small complaint', 'costs of ambition',
            'not a flaw', 'deliberate choice', 'thematically coherent'
        ])
        
        # Extract specific suggestion
        suggestion = ""
        sugg_match = re.search(r'(?:Specific\s+)?[Ss]uggestion[s]?:?\s*\n?(.*?)(?=\n\d+\.|\n\n[A-Z]|\Z)', 
                               section, re.DOTALL)
        if sugg_match:
            suggestion = sugg_match.group(1).strip()[:500]
        
        items.append({
            "number": num,
            "title": title,
            "severity": severity,
            "type": fix_type,
            "qualified": qualified,
            "suggestion": suggestion,
            "full_text": section.strip()[:1000],
        })
    
    return {
        "stars": stars,
        "critic_summary": critic_text.strip()[:500],
        "professor_items": items,
        "total_items": len(items),
        "major_items": sum(1 for i in items if i["severity"] == "major"),
        "qualified_items": sum(1 for i in items if i["qualified"]),
        "raw_text": review_text,
    }


def should_stop(parsed_review):
    """Determine if the novel is done being revised.
    
    Stopping conditions:
    - Stars >= 4.5 with no major items
    - Stars >= 4 with > half the items qualified/hedged
    - The review parsed successfully (stars present) and found <= 2 items

    A missing star rating means the review failed to parse — that is NOT a
    signal to stop; the loop should keep going (or surface the failure).
    """
    stars_raw = parsed_review.get("stars")
    stars = stars_raw or 0
    total = parsed_review["total_items"]
    major = parsed_review["major_items"]
    qualified = parsed_review["qualified_items"]
    
    if stars_raw is None:
        return False, "review did not parse a star rating — not treating as done"
    if stars >= 4.5 and major == 0:
        return True, "★★★★½ with no major items"
    if stars >= 4 and total > 0 and qualified / total > 0.5:
        return True, f"★{'★' * int(stars)} with {qualified}/{total} items qualified"
    if total <= 2:
        return True, f"Only {total} items found"
    
    return False, f"{major} major items, {total - qualified} unqualified"


def cmd_review(args):
    """Generate a review."""
    title = get_title()
    manuscript = build_manuscript()
    
    prompt = REVIEW_PROMPT.format(title=title, manuscript=manuscript)
    
    review_text = call_opus(prompt)
    
    # Save raw review
    logs_dir = paths.get_edit_logs_dir()
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"{timestamp}_review.json"
    
    parsed = parse_review(review_text)
    parsed["timestamp"] = timestamp
    parsed["title"] = title
    parsed["word_count"] = len(manuscript.split())
    
    log_path.write_text(json.dumps(parsed, indent=2, default=str))
    print(f"\nReview saved to {log_path}", file=sys.stderr)
    
    # Save human-readable copy
    if args.output:
        Path(args.output).write_text(review_text)
        print(f"Human-readable copy: {args.output}", file=sys.stderr)
    
    # Print summary
    stop, reason = should_stop(parsed)
    print(f"\n{'='*50}")
    print(f"REVIEW SUMMARY")
    print(f"  Stars: {parsed['stars']}")
    print(f"  Items: {parsed['total_items']} ({parsed['major_items']} major)")
    print(f"  Qualified: {parsed['qualified_items']}/{parsed['total_items']}")
    print(f"  Stop revising? {'YES — ' + reason if stop else 'NO — ' + reason}")
    print(f"{'='*50}")
    
    return parsed


def cmd_parse(args):
    """Parse the most recent review into actionable items."""
    logs_dir = paths.get_edit_logs_dir()
    logs_dir.mkdir(exist_ok=True)
    reviews = sorted(logs_dir.glob("*_review.json"), reverse=True)
    if not reviews:
        print("No reviews found. Run: review.py first")
        sys.exit(1)
    
    latest = json.loads(reviews[0].read_text())
    
    print(f"Latest review: {latest.get('timestamp', 'unknown')}")
    print(f"Stars: {latest.get('stars', '?')}")
    print(f"\nACTIONABLE ITEMS ({latest['total_items']}):")
    
    for item in latest.get("professor_items", []):
        qual = " [QUALIFIED]" if item["qualified"] else ""
        print(f"\n  {item['number']}. [{item['severity'].upper()}] [{item['type']}]{qual}")
        print(f"     {item['title']}")
        if item["suggestion"]:
            print(f"     Suggestion: {item['suggestion'][:120]}...")
    
    stop, reason = should_stop(latest)
    print(f"\n{'='*50}")
    print(f"Stop revising? {'YES — ' + reason if stop else 'NO — ' + reason}")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="Deep manuscript review via Opus")
    parser.add_argument("--output", "-o", default=None, help="Save human-readable review to file")
    parser.add_argument("--parse", action="store_true", help="Parse most recent review")
    
    args = parser.parse_args()
    
    if not os.environ.get("ANTHROPIC_API_KEY", ""):
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)
    
    if args.parse:
        cmd_parse(args)
    else:
        cmd_review(args)


if __name__ == "__main__":
    main()
