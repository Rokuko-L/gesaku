#!/usr/bin/env python3
"""
sanitize_outline_titles.py — Programmatic gatekeeper for chapter titles in outline.md.
Ensures title diversity (limits 'The', 'In Which', duplicates, and repeated comedic phrasing).
Runs an LLM feedback loop if violations are found.
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm, parse_json_response
from core import paths
from core.paths import format_prompt
import json
import re
import sys
import math
from pathlib import Path
from dotenv import load_dotenv
from core.genre import load_genre

load_dotenv()

COMMON_WORDS = {
    "with", "from", "over", "under", "about", "after", "through", "between",
    "before", "into", "onto", "your", "their", "them", "then", "there", "they",
    "that", "this", "these", "those", "have", "been", "were", "what", "when",
    "where", "which", "who", "whom", "whose", "why", "how", "will", "would",
    "shall", "should", "could", "might", "must", "some", "any", "each", "every",
    "both", "either", "neither", "somebody", "someone", "something", "anybody",
    "anyone", "anything", "nobody", "nothing", "everything", "everyone", "everybody"
}

DEFAULT_REWRITER_PROMPT = paths.load_prompt("sanitize_titles_rewriter")

def extract_titles(outline_text):
    """Parse chapter numbers and titles from outline.md."""
    titles = {}
    for line in outline_text.splitlines():
        raw_line = line.strip()
        # Strip markdown formatting (*, _) for the header MATCH only, so styled
        # headers (e.g. **Chapter 1:**) still parse. The title itself is read
        # from the raw line: stripping '_' globally used to destroy snake_case
        # slugs (crumble_conspiracy_report -> crumbleconspiracyreport), which
        # silently disabled the slug detector below.
        cleaned_line = raw_line.replace('*', '').replace('_', '')
        m = re.match(r'^###\s*(?:Chapter|Ch\.?)\s*(\d+)\s*:\s*(.+)$', cleaned_line, re.IGNORECASE)
        if m:
            ch_num = int(m.group(1))
            # Recover the title from the raw line: everything after the first
            # ':' following "Chapter N" (strip only trailing markdown).
            colon = raw_line.find(':')
            title = re.sub(r'^[*_\s]+', '', raw_line[colon + 1:])
            title = re.sub(r'[*_\s]+$', '', title).strip()
            titles[ch_num] = title
    return titles

def clean_words(text):
    """Normalize text into lowercase words, removing punctuation."""
    cleaned = re.sub(r'[^\w\s]', ' ', text.lower())
    return [w for w in cleaned.split() if w]

def validate_titles(titles):
    """
    Run programmatic checks on the titles dict {ch_num: title}.
    Returns a list of error strings. If list is empty, validation passes.
    """
    errors = []
    
    # 1. Duplicate check
    seen_titles = {}
    for ch, t in titles.items():
        norm = t.strip().lower()
        if norm in seen_titles:
            errors.append(f"Duplicate title found: '{t}' (used in Chapter {seen_titles[norm]} and Chapter {ch})")
        seen_titles[norm] = ch

    total_chapters = len(titles)
    if total_chapters == 0:
        return ["No chapter titles found in outline."]

    # 2. Prefix limits
    max_the = max(3, math.ceil(0.30 * total_chapters))
    max_in_which = max(2, math.ceil(0.10 * total_chapters))
    max_a_an = max(2, math.ceil(0.10 * total_chapters))

    the_chapters = []
    in_which_chapters = []
    a_an_chapters = []

    for ch, t in titles.items():
        t_lower = t.lower().strip()
        if t_lower.startswith("the "):
            the_chapters.append(f"Ch {ch} ('{t}')")
        elif t_lower.startswith("in which "):
            in_which_chapters.append(f"Ch {ch} ('{t}')")
        elif t_lower.startswith("a ") or t_lower.startswith("an "):
            a_an_chapters.append(f"Ch {ch} ('{t}')")

    if len(the_chapters) > max_the:
        errors.append(
            f"Too many titles start with 'The' ({len(the_chapters)} out of {total_chapters}, limit is {max_the}). "
            f"Violators: {', '.join(the_chapters)}"
        )
    if len(in_which_chapters) > max_in_which:
        errors.append(
            f"Too many titles start with 'In Which' ({len(in_which_chapters)} out of {total_chapters}, limit is {max_in_which}). "
            f"Violators: {', '.join(in_which_chapters)}"
        )
    if len(a_an_chapters) > max_a_an:
        errors.append(
            f"Too many titles start with 'A' or 'An' ({len(a_an_chapters)} out of {total_chapters}, limit is {max_a_an}). "
            f"Violators: {', '.join(a_an_chapters)}"
        )

    # 3. Repeated 3-word phrases (comedic template duplication check)
    phrase_map = {}
    for ch, t in titles.items():
        words = clean_words(t)
        # Extract all 3-word n-grams
        for i in range(len(words) - 2):
            phrase = " ".join(words[i:i+3])
            if phrase not in phrase_map:
                phrase_map[phrase] = []
            phrase_map[phrase].append(ch)

    for phrase, chapters in phrase_map.items():
        if len(chapters) > 1:
            # Only flag if it's not a generic sequence of tiny words
            if any(len(w) >= 4 for w in phrase.split()):
                errors.append(
                    f"Repeated 3-word phrase '{phrase}' found in multiple chapters: {', '.join(f'Ch {c}' for c in chapters)}"
                )

    # 4. Repeated non-trivial single words (frequency limit)
    word_map = {}
    for ch, t in titles.items():
        words = set(clean_words(t)) # unique words per title
        for w in words:
            if len(w) >= 4 and w not in COMMON_WORDS:
                if w not in word_map:
                    word_map[w] = []
                word_map[w].append(ch)

    for word, chapters in word_map.items():
        if len(chapters) > 2:
            errors.append(
                f"Word '{word}' is repeated too frequently ({len(chapters)} times, limit is 2). "
                f"Chapters: {', '.join(f'Ch {c}' for c in chapters)}"
            )

    # 5. Snake_case slug titles (unfilled outline codenames, e.g.
    #    "shadow_compact_ambush") — these get printed verbatim on the PDF.
    slug_re = re.compile(r"[a-z]_[a-z]")
    for ch, t in sorted(titles.items()):
        if slug_re.search(t):
            errors.append(
                f"Chapter {ch} title '{t}' is a snake_case codename (an unfilled outline "
                f"slug) — replace it with a real human-readable title."
            )

    return errors

def main():
    outline_path = paths.get_outline_path()
    if not outline_path.exists():
        print(f"ERROR: outline.md not found at {outline_path}", file=sys.stderr)
        sys.exit(1)

    seed_path = paths.get_seed_path()
    seed_text = seed_path.read_text(encoding="utf-8") if seed_path.exists() else ""
    outline_text = outline_path.read_text(encoding="utf-8")

    current_titles = extract_titles(outline_text)
    if not current_titles:
        print("No chapter titles found in outline.md to sanitize.", file=sys.stderr)
        return

    print(f"Parsed {len(current_titles)} chapter titles from outline.md.")
    
    errors = validate_titles(current_titles)
    if not errors:
        print("[OK] Chapter titles already satisfy all diversity rules.")
        return

    print("[FAIL] Chapter titles failed validation. Running LLM sanitization...")
    for err in errors:
        print(f"  - {err}")

    # Load active genre prompt, or fallback to default
    genre_cfg = load_genre()
    template = genre_cfg.get("generation", {}).get("gen_chapter_title_rewriter_prompt", "")
    if not template or len(template.strip()) < 30:
        print("Using default chapter title rewriter prompt template.")
        template = DEFAULT_REWRITER_PROMPT
    else:
        print("Using genre-specific chapter title rewriter prompt template.")

    # Call LLM loop
    MAX_ATTEMPTS = 5
    attempts_errors = list(errors)
    
    success = False
    new_titles = {}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"Sanitization attempt {attempt}/{MAX_ATTEMPTS}...", file=sys.stderr)
        
        # Build prompt
        formatted_titles = "\n".join(f"Chapter {ch}: {t}" for ch, t in sorted(current_titles.items()))
        prompt = format_prompt(
            template,
            outline=outline_text,
            seed=seed_text,
            current_titles=formatted_titles
        )
        
        # Inject feedback
        prompt += f"\n\n=== REQUIRED FIXES ===\nYour previous titles failed validation with these errors:\n"
        for err in attempts_errors:
            prompt += f"- {err}\n"
        prompt += (
            "\nPlease rewrite the chapter titles to be catchy and varied. "
            "Ensure that: \n"
            f"1. At most {max(3, math.ceil(0.3 * len(current_titles)))} titles start with 'The'.\n"
            f"2. At most {max(2, math.ceil(0.1 * len(current_titles)))} titles start with 'In Which'.\n"
            f"3. At most {max(2, math.ceil(0.1 * len(current_titles)))} titles start with 'A' or 'An'.\n"
            "4. Distinctive words are not repeated more than twice.\n"
            "5. Output MUST be valid JSON mapping strings of chapter numbers to their new titles."
        )
        
        try:
            raw_response = call_llm(
                prompt=prompt,
                system="You are a meticulous book editor who outputs valid JSON only.",
                model_key="writer",  # Use writer model for creative title rewriting
                max_tokens=4000,
                temperature=0.8,
                timeout_role="short"
            )
            
            raw_json = parse_json_response(raw_response)
            
            # Map keys to integers and check
            proposed_titles = {}
            for k, v in raw_json.items():
                proposed_titles[int(k)] = str(v).strip()
                
            # Fill missing entries from original titles if any
            for ch in current_titles:
                if ch not in proposed_titles:
                    proposed_titles[ch] = current_titles[ch]
                    
            # Validate
            new_errors = validate_titles(proposed_titles)
            if not new_errors:
                new_titles = proposed_titles
                success = True
                print(f"[OK] Validation passed on attempt {attempt}!")
                break
            else:
                attempts_errors = new_errors
                print(f"  Attempt {attempt} proposed titles failed validation: {len(new_errors)} errors.", file=sys.stderr)
                for err in new_errors[:3]:
                    print(f"    Error: {err}", file=sys.stderr)
                # Print a clean JSON representation of the proposed titles for easy reading
                print(f"    Proposed titles:\n{json.dumps(proposed_titles, indent=2)}", file=sys.stderr)
                
        except Exception as e:
            print(f"  Attempt {attempt} error: {e}", file=sys.stderr)
            attempts_errors = [f"LLM call or JSON parsing failed: {e}"]

    if not success:
        print("[FAIL] FAILED to sanitize titles after max attempts. Retaining original titles to prevent pipeline crash.", file=sys.stderr)
        sys.exit(0) # Exit cleanly to let pipeline continue, but notify in stdout

    # Apply changes to outline.md in-place
    lines = outline_text.splitlines()
    replaced_count = 0
    for i, line in enumerate(lines):
        # Strip markdown formatting (*, _) to match the chapter format
        cleaned_line = line.strip().replace('*', '').replace('_', '')
        m = re.match(r'^###\s*(?:Chapter|Ch\.?)\s*(\d+)\s*:\s*(.+)$', cleaned_line, re.IGNORECASE)
        if m:
            ch = int(m.group(1))
            parts = line.split(':', 1)
            prefix = parts[0]
            # Safely capture any formatting suffixes (e.g. trailing **)
            suffix = ""
            if line.endswith('**'):
                suffix = "**"
            elif line.endswith('*'):
                suffix = "*"
            
            if ch in new_titles and ch in current_titles and new_titles[ch] != current_titles[ch]:
                lines[i] = f"{prefix}: {new_titles[ch]}{suffix}"
                replaced_count += 1
                
    if replaced_count > 0:
        outline_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Successfully sanitized outline.md. Renamed {replaced_count} chapter titles:")
        for ch in sorted(new_titles.keys()):
            if ch in current_titles and new_titles[ch] != current_titles[ch]:
                print(f"  Ch {ch}: '{current_titles[ch]}' -> '{new_titles[ch]}'")
    else:
        print("Sanitization succeeded, but no titles needed to be changed.")

if __name__ == "__main__":
    main()
