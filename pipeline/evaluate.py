#!/usr/bin/env python3
"""
evaluate.py -- Novel evaluation harness.

Usage:
  python evaluate.py --phase=foundation    # Score planning docs only
  python evaluate.py --chapter=5           # Score a single chapter
  python evaluate.py --full                # Score the entire novel

Output: structured scores to stdout + eval_logs/<timestamp>.json

This file is READ-ONLY during autonomous runs. The human edits it
to tune what "good" means. The agent treats it as a black box.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import TruncationError, call_llm, extract_text_from_response, get_max_tokens_with_thinking, parse_json_response
from core import paths
from core import textstats
from core import canon as canon_mod
import argparse
import json
import os
import sys
import glob
import re
from datetime import datetime
from pathlib import Path

# --- Configuration ---

# Load .env file if present
from dotenv import load_dotenv
load_dotenv()
from core.genre import load_genre
from core import validation


# ---- Mechanical Slop Detection (no LLM needed) ----

TIER1_BANNED = [
    "delve", "utilize", "leverage", "facilitate", "elucidate",
    "embark", "endeavor", "encompass", "multifaceted", "tapestry",
    "paradigm", "synergy", "synergize", "holistic", "catalyze",
    "catalyst", "juxtapose", "myriad", "plethora",
]

TIER2_SUSPICIOUS = [
    "robust", "comprehensive", "seamless", "seamlessly", "cutting-edge",
    "innovative", "streamline", "empower", "foster", "enhance", "elevate",
    "optimize", "pivotal", "intricate", "profound", "resonate",
    "underscore", "harness", "cultivate", "bolster", "galvanize",
    "cornerstone", "game-changer", "scalable",
]

TIER3_FILLER = [
    r"it'?s worth noting that",
    r"it'?s important to note that",
    r"^importantly,?\s",
    r"^notably,?\s",
    r"^interestingly,?\s",
    r"let'?s dive into",
    r"let'?s explore",
    r"as we can see",
    r"^furthermore,?\s",
    r"^moreover,?\s",
    r"^additionally,?\s",
    r"in today'?s .*(fast-paced|digital|modern)",
    r"at the end of the day",
    r"it goes without saying",
    r"when it comes to",
    r"one might argue that",
    # (not just .+, but — covered by STRUCTURAL_AI_TICS below)
    # Conversational rhetoric openers (Humanizer #33)
    r"^Honestly\?[\s,]",
    r"^Truthfully[?,]\s",
    r"^Look,?\s",
]

TRANSITION_OPENERS = [
    "however", "furthermore", "additionally", "moreover",
    "nevertheless", "consequently", "nonetheless", "similarly",
]

# Fiction-specific AI tells (prose clichés that betray machine origin)
FICTION_AI_TELLS = [
    r"a sense of \w+",
    r"couldn'?t help but feel",
    r"the weight of \w+",
    r"the air was thick with",
    r"eyes widened",
    r"a wave of \w+ washed over",
    r"a pang of \w+",
    r"heart pounded in (?:his|her|their) chest",
    r"(?:raven|dark|golden|silver) (?:hair|tresses) (?:spilled|cascaded|tumbled|fell)",
    r"piercing (?:blue|green|gray|grey|dark) eyes",
    r"a knowing (?:smile|grin|look|glance)",
    r"(?:he|she|they) felt a (?:surge|rush|wave|pang|flicker) of",
    r"the silence (?:was|hung|stretched|grew) (?:heavy|thick|oppressive|deafening)",
    r"let out a breath (?:he|she|they) didn'?t (?:know|realize)",
    r"something (?:dark|ancient|primal|unnamed) stirred",
    # Copula avoidance -- "serves as" instead of "is" (Humanizer #8)
    r"\b(?:serves as|serves to|stands as|acts as|functions as)\b",
    # Generic capstone conclusions (Humanizer #25)
    r"the future (?:looked|seemed|promised|appeared)",
]

# Structural AI tics -- rhetorical formulas that betray AI composition
STRUCTURAL_AI_TICS = [
    r"(?:I'm|I am) not (?:saying|asking|suggesting) .{3,40}(?:I'm|I am) (?:saying|asking|suggesting)",  # "I'm not saying X. I'm saying Y"
    r"(?:which|that) means either .{3,40} or ",  # "which means either X, or Y"
    r"[Tt]here'?s a (?:difference|distinction)\.",  # formula capper
    r"[Tt]hose are (?:different|not the same) things\.",  # formula capper
    r"[Nn]ot (?:just|merely|simply) .{3,40}, but ",  # "not just X, but Y"
    r"[Nn]ot (?:from|by|because of) .{3,40}, but (?:from|by|because)",  # "not from X, but from Y" in narration
    # Authority framing (Humanizer #27)
    r"^At its core,?",
    r"^The truth is,?",
    r"^What matters is,?",
    r"^The fact (?:is|remains),?",
    # Aphorism formulas (Humanizer #32)
    r"\b(?:is|was) the (?:language|art|science|essence|foundation|soul|hallmark|bedrock|currency) of\b",
]

# Prose tic families -- rhetorical constructions that are fine once but betray
# machine origin in clusters. Detected with density thresholds (see below).
# NOTE: these are NOT banned outright; a single "not X, but Y" is normal human
# prose. The tell is repetition within a chapter.

PROSE_TIC_PATTERNS = [
    # "not X, but Y" (bare form -- "It did not arrive as sound, but as a blow")
    ("not_but", r"[Nn]ot [a-z][^.,!?;]{2,60}?,\s*but "),
    # Stacked negation ("Not a melody, not a hum, but a deep vibration")
    ("stacked_negation", r"[Nn]ot [a-z][^.,!?;]{2,50}?,\s*(?:not|nor) [a-z]"),
    # "not X so much as Y" ("not a torrent so much as a whine")
    ("not_so_much_as", r"not [a-z][^.,!?;]{2,60}?,\s*so much as "),
    # Abstract-noun frame ("the sound of", "the shape of", "the weight of" as
    # a rhetorical device, not a literal reference)
    ("x_of_y_frame", r"\bthe (?:sound|shape|weight|color|smell|feel|taste|music|language|art|science|essence|soul|fabric|texture|rhythm|echo|hint|whisper|scent|flavor) of\b"),
    # "a thing of X and Y" descriptor frame ("a thing of jagged edges and creeping shadow")
    ("thing_of", r"\b(?:a|an) (?:thing|creature|woman|girl|man|place|room|cat|beast|girl|boy) of [a-z]+ and [a-z]+"),
]

# Density thresholds: instances per 3000 words that start costing points.
# Below threshold = normal human variation. Above = tic.
PROSE_TIC_THRESHOLDS = {
    "not_but": 2.0,          # >2 per 3k words penalized
    "stacked_negation": 1.0, # >1 per 3k words penalized
    "not_so_much_as": 1.0,
    "x_of_y_frame": 3.0,     # >3 per 3k words penalized (some are literal)
    "thing_of": 1.0,
}

PROSE_TIC_PENALTY_PER_INSTANCE = {
    "not_but": 0.35,
    "stacked_negation": 0.45,
    "not_so_much_as": 0.45,
    "x_of_y_frame": 0.25,
    "thing_of": 0.4,
}

PROSE_TIC_CAPS = {
    "not_but": 1.5,
    "stacked_negation": 1.2,
    "not_so_much_as": 1.0,
    "x_of_y_frame": 1.0,
    "thing_of": 0.8,
}


def prose_tics(text):
    """Density-based detection of rhetorical tic families.

    Returns (tics, extra_penalty) where tics is a list of
    (tic_name, count, per_3k) and extra_penalty is the added deduction.
    """
    word_count = len(text.split()) or 1
    scale = 3000.0 / word_count
    tics = []
    extra_penalty = 0.0
    for name, pattern in PROSE_TIC_PATTERNS:
        count = len(re.findall(pattern, text))
        if count == 0:
            continue
        per_3k = count * scale
        tics.append((name, count, round(per_3k, 2)))
        threshold = PROSE_TIC_THRESHOLDS[name]
        if per_3k > threshold:
            over = per_3k - threshold
            extra_penalty += min(over * PROSE_TIC_PENALTY_PER_INSTANCE[name],
                                 PROSE_TIC_CAPS[name])
    # Non-Latin script (CJK, Cyrillic, Arabic, etc.) injected mid-prose by
    # multilingual writer models. EBGaramond has no glyphs for these — they
    # render as blanks in the PDF. Flag as a tic so the retry loop removes them.
    non_latin_hits = re.findall(
        r'[\u2E80-\u9FFF\uAC00-\uD7AF\u0400-\u04FF\u0600-\u06FF\u0900-\u097F\u3040-\u30FF\u0E00-\u0E7F]+',
        text,
    )
    if non_latin_hits:
        count = len(non_latin_hits)
        per_3k = count * scale
        tics.append(('non_latin_script', count, round(per_3k, 2)))
        extra_penalty += min(per_3k * 0.15, 1.5)
    return tics, round(extra_penalty, 2)


# Show-don't-tell detectors: emotion TELLING patterns
TELLING_PATTERNS = [
    r"\b(?:he|she|they|I|we|[A-Z]\w+) (?:felt|was|seemed|looked|appeared) (?:angry|sad|happy|scared|nervous|excited|jealous|guilty|anxious|lonely|desperate|furious|terrified|elated|miserable|hopeful|confused|relieved|horrified|disgusted|ashamed|proud|bitter|defeated|triumphant)\b",
    r"\b(?:angrily|sadly|happily|nervously|excitedly|desperately|furiously|anxiously|guiltily|bitterly|wearily|miserably)\b",
]


def slop_score(text):
    """
    Mechanical slop detection. Returns a dict with:
      - tier1_hits: list of (word, count)
      - tier2_hits: list of (word, count)
      - tier3_hits: list of (pattern, count)
      - em_dash_density: em dashes per 1000 words
      - sentence_length_cv: coefficient of variation (higher = more human)
      - transition_opener_ratio: fraction of paragraphs starting with transitions
      - slop_penalty: 0-10 deduction (0 = clean, 10 = pure slop)
    """
    words = text.lower().split()
    word_count = len(words) or 1

    # Tier 1
    tier1_hits = []
    for w in TIER1_BANNED:
        c = sum(1 for token in words if token.strip(".,;:!?\"'()") == w)
        if c > 0:
            tier1_hits.append((w, c))

    # Tier 2 -- count per paragraph, flag clusters
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    tier2_hits = []
    tier2_cluster_count = 0
    for w in TIER2_SUSPICIOUS:
        c = sum(1 for token in words if token.strip(".,;:!?\"'()") == w)
        if c > 0:
            tier2_hits.append((w, c))
    for para in paragraphs:
        para_lower = para.lower()
        hits_in_para = sum(1 for w in TIER2_SUSPICIOUS if w in para_lower)
        if hits_in_para >= 3:
            tier2_cluster_count += 1

    # Tier 3
    tier3_hits = []
    for pattern in TIER3_FILLER:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        if matches:
            tier3_hits.append((pattern, len(matches)))

    # Em dash density
    em_dashes = text.count("—") + text.count("--")
    em_dash_density = (em_dashes / word_count) * 1000

    # Sentence length variation (coefficient of variation)
    sentences = re.split(r'[.!?]+', text.replace("...", " ").replace("..", " "))
    sentences = [s.strip() for s in sentences if len(s.strip().split()) > 2]
    if len(sentences) > 2:
        lengths = [len(s.split()) for s in sentences]
        mean_len = sum(lengths) / len(lengths)
        variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
        std_len = variance ** 0.5
        sentence_length_cv = std_len / mean_len if mean_len > 0 else 0
    else:
        sentence_length_cv = 0.5  # not enough data, assume OK

    # Transition opener ratio
    transition_starts = 0
    for para in paragraphs:
        first_word = para.split()[0].lower().strip(".,;:!?\"'()") if para.split() else ""
        if first_word in TRANSITION_OPENERS:
            transition_starts += 1
    transition_ratio = transition_starts / len(paragraphs) if paragraphs else 0

    # Fiction AI tells
    fiction_tells = []
    for pattern in FICTION_AI_TELLS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            fiction_tells.append((pattern[:40], len(matches)))
    fiction_tell_count = sum(c for _, c in fiction_tells)

    # Show-don't-tell violations
    telling_count = 0
    for pattern in TELLING_PATTERNS:
        telling_count += len(re.findall(pattern, text, re.IGNORECASE))

    # Structural AI tics (rhetorical formulas)
    structural_tics = []
    for pattern in STRUCTURAL_AI_TICS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            structural_tics.append((pattern[:40], len(matches)))
    structural_tic_count = sum(c for _, c in structural_tics)

    # Staccato punchline detector (Humanizer #31) — 3+ consecutive sentences ≤4 words
    # Count EVERY run of 3+ short sentences (a run of k short sentences = k-2 instances),
    # including runs that continue past the initial trigger.
    staccato_runs = 0
    for para in paragraphs:
        para_clean = para.replace("...", " ").replace("..", " ")
        para_sents = [s.strip() for s in re.split(r'[.!?]+', para_clean) if any(c.isalnum() for c in s)]
        run = 0
        for s in para_sents:
            if len(s.split()) <= 4:
                run += 1
            else:
                if run >= 3:
                    staccato_runs += run - 2
                run = 0
        if run >= 3:
            staccato_runs += run - 2

    # Scale absolute counts to a density basis (per 3,000 words) to prevent manuscript length inflation
    scale = 3000.0 / word_count

    # Composite penalty (0 = clean, 10 = disaster)
    # Global cap: 4.0 — enough to push a sloppy chapter below the 6.5 gate
    # without letting one failure mode single-handedly zero a good chapter.
    penalty = 0.0
    penalty += min((len(tier1_hits) * scale) * 1.5, 4.0)       # tier1: up to 4 pts
    penalty += min((tier2_cluster_count * scale) * 1.0, 2.0)    # tier2 clusters: up to 2 pts
    penalty += min((sum(c for _, c in tier3_hits) * scale) * 0.3, 2.0)  # tier3: up to 2 pts
    if em_dash_density > 15:
        penalty += min((em_dash_density - 15) * 0.3, 1.0)  # em dashes: up to 1 pt (threshold raised for voice)
    if sentence_length_cv < 0.3:
        penalty += 1.0  # uniform sentence length: 1 pt
    if transition_ratio > 0.3:
        penalty += min(transition_ratio * 2, 1.0)  # transition abuse: up to 1 pt
    penalty += min((fiction_tell_count * scale) * 0.3, 2.0)     # fiction AI tells: up to 2 pts
    penalty += min((telling_count * scale) * 0.2, 1.5)          # show-don't-tell: up to 1.5 pts
    penalty += min((structural_tic_count * scale) * 0.5, 2.0)   # structural AI tics: up to 2 pts
    penalty += min((staccato_runs * scale) * 0.08, 2.0)          # staccato punchlines: up to 2 pts

    # Prose tic families (density-based) -- the "reads like AI" constructions
    prose_tics_found, tic_penalty = prose_tics(text)
    penalty += min(tic_penalty, 3.0)

    penalty = min(penalty, 4.0)

    return {
        "tier1_hits": tier1_hits,
        "tier2_hits": tier2_hits,
        "tier2_clusters": tier2_cluster_count,
        "tier3_hits": tier3_hits,
        "fiction_ai_tells": fiction_tells,
        "structural_ai_tics": structural_tics,
        "staccato_runs": staccato_runs,
        "telling_violations": telling_count,
        "prose_tics": [{"tic": n, "count": c, "per_3k": p} for n, c, p in prose_tics_found],
        "prose_tic_penalty": tic_penalty,
        "em_dash_density": round(em_dash_density, 2),
        "sentence_length_cv": round(sentence_length_cv, 3),
        "transition_opener_ratio": round(transition_ratio, 3),
        "slop_penalty": round(penalty, 2),
    }


def load_file(path):
    """Load a text file, return empty string if missing, with robust encoding recovery and self-healing."""
    path = Path(path)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_bytes()
        for enc in ("utf-16", "utf-16-le", "utf-16-be", "latin-1"):
            try:
                text = raw.decode(enc).lstrip("\ufeff")
                # Self-heal: rewrite as clean UTF-8
                path.write_text(text, encoding="utf-8")
                print(f"[ENCODING] Repaired {path.name}: was {enc}, now UTF-8", file=sys.stderr)
                return text
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Could not decode {path} with any known encoding")


def load_layer_files():
    """Load all planning layer files from the active project directory."""
    return {
        "voice": load_file(paths.get_voice_path()),
        "world": load_file(paths.get_world_path()),
        "characters": load_file(paths.get_characters_path()),
        "outline": load_file(paths.get_outline_path()),
        "canon": load_file(paths.get_canon_path()),
    }


def load_chapter(n):
    """Load a single chapter file from the active project."""
    return load_file(paths.get_chapters_dir() / f"ch_{n:02d}.md")


def load_all_chapters():
    """Load all chapter files in order from the active project."""
    chapters_dir = paths.get_chapters_dir()
    chapters = {}
    for f in sorted(glob.glob(str(chapters_dir / "ch_*.md"))):
        num = int(re.search(r'ch_(\d+)', f).group(1))
        try:
            chapters[num] = load_file(Path(f))
        except ValueError as e:
            raise RuntimeError(f"FATAL: chapter file {f} (ch {num}) is unreadable: {e}")
    return chapters


def call_judge(prompt, max_tokens=2000):
    genre_cfg = load_genre()
    system = genre_cfg["identity"]["evaluator_system"]
    perspective = genre_cfg.get("perspective", "")
    if perspective:
        expected = "first-person ('I/me/my' narration by the POV character)" if perspective == "first_person" else "third-person limited (he/she/they, anchored to the POV character's head)"
        system += (f"\n\nPERSPECTIVE RULE: The novel is mandated {expected}. If the chapter drifts "
                   "out of this narration mode, flag it under prose_quality or voice_adherence "
                   "with a specific quote of the offending passage.")
    from core.genre import prose_mode_system_block
    prose_block = prose_mode_system_block(genre_cfg)
    if prose_block:
        system += (prose_block +
                   "\n\nPROSE MODE RULE: Score against this pack. Penalize staccato 1–4 word "
                   "paragraph stacks, empty emotion labels, diary-summary interiority, wrong "
                   "narrative distance, and repeated stock metaphors. Quote offenders.")
    return call_llm(prompt=prompt, system=system, model_key="judge", max_tokens=max_tokens, beta_context=True, timeout_role="xlong")


def call_judge_json(prompt, max_tokens=8000, retries=3, model=None):
    """Call the judge and return its JSON as a dict.

    When `model` (a validation.ScoreOutput / NovelScoreOutput subclass) is
    given, each response is schema-validated; shape failures join syntax
    failures in the LLM self-correction retry loop. The validated model is
    returned as a dict so downstream dict access keeps working.
    """
    last_raw = None
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            if attempt == 1 or not last_raw or not last_raw.strip():
                raw = call_judge(prompt, max_tokens)
            else:
                # Ask the model to fix its previous response (using a cheap, lightweight context prompt)
                fix_prompt = f"""You previously returned a response that had invalid JSON syntax.
The parser returned this error: {last_error}

YOUR PREVIOUS RESPONSE:
{last_raw}

TASK:
Correct the JSON syntax errors in your previous response. Respond ONLY with the corrected, valid JSON object. Do not include any explanation or conversational text outside the JSON. Ensure all quotes inside string values are properly escaped (e.g. use \\" instead of ")."""
                # Dynamically calculate a token limit for the fix call
                tokens_needed = max(2000, (len(last_raw) // 3) + 200)
                max_tokens_fix = min(max_tokens, tokens_needed)
                
                raw = call_judge(fix_prompt, max_tokens_fix)
            
            last_raw = raw
            result = parse_json_response(raw)
            if model is not None:
                validated = validation.parse_validated(model, raw, context="Judge response")
                return validated.model_dump()
            return result
        except TruncationError:
            # Response hit the token cap. Retrying with the SAME-or-smaller budget is
            # guaranteed to truncate again — re-ask the original prompt with more room.
            if attempt == retries:
                raise
            print(f"Judge response truncated on attempt {attempt}/{retries} — "
                  f"retrying original prompt with a larger output budget "
                  f"({max_tokens} -> {int(max_tokens * 1.5)})", file=sys.stderr)
            max_tokens = int(max_tokens * 1.5)
            last_raw = None
        except (json.JSONDecodeError, ValueError) as e:
            last_error = str(e)
            if attempt == retries:
                raise e
            print(f"JSON decode failed on attempt {attempt}/{retries}: {e}. Retrying LLM self-correction...", file=sys.stderr)


# --- Foundation Evaluation ---

def build_foundation_prompt():
    cfg = load_genre()
    ecfg = cfg["evaluation"]["foundation"]
    prompt = ecfg["overall_calibration"] + "\n\n"

    prompt += """VOICE DEFINITION:
{voice}

WORLD BIBLE:
{world}

CHARACTER REGISTRY:
{characters}

OUTLINE:
{outline}

CANON (established facts):
{canon}

CROSS-CHECKS (perform these before scoring):
1. Check all example dialogue lines against ANTI-SLOP patterns
2. Check for missing NEGATIVE SPACE
3. Check for CONVENIENT GAPS vs DELIBERATE MYSTERY
4. Check the canon for INTERNAL CONTRADICTIONS

Score these dimensions (gap + improvement required for each):

"""
    for dim in ecfg["dimensions"]:
        prompt += f"- {dim['key'].replace('_', ' ').title()}: {dim['criteria']}\n\n"

    prompt += f"""
Respond with JSON:
{{
  "overall_score": N,
  "lore_score": N,
{chr(10).join(f'  "{dim["key"]}": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},' for dim in ecfg["dimensions"])}
  "slop_in_planning_docs": {{"found": ["list any AI slop patterns"], "note": "..."}},
  "contradictions_found": ["list any factual contradictions"],
  "weakest_dimension": "...",
  "top_3_improvements": ["ranked list of improvements"]
}}

CRITICAL FORMATTING GUIDELINES:
1. Output ONLY valid JSON matching the exact schema above.
2. Escape any double quotes within your JSON string values with a backslash (e.g., use \\" instead of " when referencing characters, quotes, or dialogue).
3. Do not include any preamble, introduction, or conversation outside the JSON object.

WEIGHTING: {" + ".join(f'{dim["key"].replace("_"," ").title()} {dim["weight"]*100:.0f}%' for dim in ecfg["dimensions"])}.

FINAL CHECK: If your overall_score is above 7, re-read your gap lists.
If any gap describes a problem that would force a writer to stop and
invent something during drafting, your score is too high. Revise down.
"""
    return prompt


def evaluate_foundation():
    layers = load_layer_files()
    prompt = build_foundation_prompt()
    for key, val in layers.items():
        prompt = prompt.replace(f"{{{key}}}", val)
    return call_judge_json(prompt, max_tokens=16000, model=validation.ScoreOutput)


# --- Chapter Evaluation ---

def build_chapter_prompt(voice, world, characters, canon_view, chapter_outline, prev_chapter_tail, chapter_text, debt_warnings=None):
    cfg = load_genre()
    ccfg = cfg["evaluation"]["chapter"]
    prompt = ccfg["overall_calibration"] + "\n\n"

    if debt_warnings:
        prompt += f"CRITICAL REQUIREMENT: This chapter MUST resolve the following active narrative setups/debts:\n"
        prompt += "\n".join(f"- {w}" for w in debt_warnings)
        prompt += "\nIf the chapter fails to clearly resolve these setups, dock the overall score significantly and explain why in your critique.\n\n"

    prompt += f"""VOICE DEFINITION:
{voice}

WORLD BIBLE (summary):
{world}

CHARACTER REGISTRY:
{characters}

CANON AS OF THIS CHAPTER (hard facts the reader may already know — violations are bugs.
Sealed author truths are withheld on purpose; do not treat missing secrets as errors,
and do not reward prose for leaking them early):
{canon_view}

CHAPTER OUTLINE ENTRY:
{chapter_outline}

PREVIOUS CHAPTER (last ~600 words):
{prev_chapter_tail}

THE CHAPTER TO EVALUATE:
{chapter_text}

CANON-GROUNDING RULES (read before scoring):
- new_canon_entries: Each entry is an object with a "fact" string and a "scope" that is either "core" or "incremental".
  - core:     Permanent world rules, character relationships, secrets, faction alignments,
              magic system rules — facts that are immutably true for the rest of the story.
  - incremental: Plot-level reveals, scene-specific reactions, temporary states, intermediate
              discoveries that later chapters may supersede or contradict.
  If in doubt, default to "incremental". Only mark as "core" if the fact is foundational
  and will never change.
  Record only what was explicitly shown or stated in this chapter's text. Never record
  background facts from the world/character bible that haven't put on the page.
- unexplained_references: Names, titles, or terms used in this chapter whose meaning
  a first-time reader would not yet understand (e.g. if a character is addressed as "the Saint"
  but the role hasn't been explained yet).
- Do not penalize the chapter for failing to reveal or use facts that are not in the
  CANON AS OF THIS CHAPTER block. Do not dock for "over-caution" about unrevealed truths.

CROSS-CHECKS (perform before scoring):
1. QUOTE TEST: Find the 3 best sentences and 3 weakest sentences.
2. DIALOGUE REALISM: Read all dialogue aloud (mentally).
3. SCENE VS SUMMARY: How much is in-scene vs summary?
4. AI PATTERN CHECK: Common AI writing patterns.
5. EARNED VS GIVEN: Is tension earned or asserted?

Score these dimensions:

"""
    for dim in ccfg["dimensions"]:
        prompt += f"- {dim['key'].replace('_', ' ').title()}: {dim['criteria']}\n\n"

    prompt += f"""
Respond with JSON:
{{
  "overall_score": N,
{chr(10).join(f'  "{dim["key"]}": {{"score": N, "weakest_moment": "...", "fix": "...", "note": "..."}},' for dim in ccfg["dimensions"])}
  "three_weakest_sentences": ["quote 1", "quote 2", "quote 3"],
  "three_strongest_sentences": ["quote 1", "quote 2", "quote 3"],
  "ai_patterns_detected": ["list any AI writing patterns found"],
  "weakest_dimension": "...",
  "top_3_revisions": ["specific revision 1", "revision 2", "revision 3"],
  "new_canon_entries": [{{"fact": "new fact description", "scope": "core|incremental"}}],
  "unexplained_references": ["names, titles, or terms used in this chapter that were not explained"]
}}

CRITICAL FORMATTING GUIDELINES:
1. Output ONLY valid JSON matching the exact schema above.
2. Escape any double quotes within your JSON string values with a backslash (e.g., use \\" instead of " when referencing characters, quotes, or dialogue).
3. Do not include any preamble, introduction, or conversation outside the JSON object.

SCORING CALIBRATION:
- Evaluate the chapter based on the overall balance of strengths and weaknesses across all dimensions. Do not let minor, easily fixable stylistic flaws or trivial editor notes artificially cap the score at 7 or below.
- Reserve scores of 8.0+ for chapters that are structurally sound, align well with the voice and characters, and successfully cover their core narrative beats.
- If a chapter has a significant, core-level failure in a defined category (such as a major plot/continuity contradiction, complete failure to cover outline beats, or major voice derailment), the overall_score should not exceed 7.0. Otherwise, score the chapter proportionally to its actual quality.
"""
    return prompt


def check_orientation_facts(chapter_text, chapter_outline):
    """
    Check if the orientation facts from the outline appear in the chapter text.
    Returns a list of failed facts.

    Matching is synonym-aware: a fact passes if any of its key content words
    OR a synonym appears in the chapter, so paraphrases like "the old powers
    stirred" satisfy "Ancient beings sense the weakening barrier".
    """
    import re
    match = re.search(r'Orientation\s+Facts\s*(?:\*\*)?:\s*(.*?)(?=\n\s*(?:\d+\.\s*)?\*\*|\Z)', chapter_outline, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    
    facts = []
    for line in match.group(1).splitlines():
        line = line.strip().lstrip('-*').strip()
        if line:
            facts.append(line)
            
    if not facts:
        return []
        
    text_lower = chapter_text.lower()
    failed_facts = []
    
    # Common stop words to ignore — including sentence-initial determiners and
    # pronouns, which are capitalized in the fact list ("The", "A", "She", "He")
    # and would otherwise match ANY chapter text, passing every fact.
    stop_words = {
        "with", "from", "over", "under", "about", "after", "through", "between",
        "before", "into", "onto", "your", "their", "them", "then", "there", "they",
        "that", "this", "these", "those", "have", "been", "were", "what", "when",
        "where", "which", "who", "whom", "whose", "why", "how", "will", "would",
        "shall", "should", "could", "might", "must", "some", "any", "each", "every",
        "both", "either", "neither", "somebody", "someone", "something", "anybody",
        "anyone", "anything", "nobody", "nothing", "everything", "everyone", "everybody",
        "the", "a", "an", "and", "of", "to", "in", "for", "on", "at", "by", "as",
        "or", "but", "not", "no", "so", "if", "then", "than", "she", "he", "it",
        "we", "you", "i", "my", "your", "his", "her", "our", "its", "their", "me",
        "him", "us", "them", "with", "was", "is", "are", "be", "been", "being", "had",
    }
    
    # Synonym families for common fantasy concepts — a fact passes if any key
    # word OR any synonym appears in the text, so paraphrase-heavy drafts are
    # not falsely penalized for not using the outline's exact vocabulary.
    synonym_map = {
        "ancient": {"ancient", "old", "elder", "deep", "primordial", "vast", "primeval"},
        "beings": {"beings", "powers", "ones", "things", "creatures", "gods", "spirits",
                   "entities", "olders", "watchers", "forces"},
        "barrier": {"barrier", "veil", "boundary", "wall", "seal", "curtain", "divide",
                    "fabric", "membrane"},
        "weaken": {"weaken", "weakening", "weakened", "crack", "cracks", "cracked",
                   "cracking", "fracture", "fracturing", "thin", "thinning", "strained",
                   "strain", "failing", "tear", "tearing", "shudder", "shuddering"},
        "sense": {"sense", "senses", "sensed", "sensing", "feel", "feels", "felt",
                  "feeling", "perceive", "perceived", "notice", "notices", "noticed",
                  "stir", "stirs", "stirred", "stirring", "awaken", "awoke",
                  "turned", "turn", "attention"},
        "hunt": {"hunt", "hunts", "hunted", "hunting", "hunter", "hunters", "track",
                 "tracks", "tracked", "tracking", "pursue", "pursuit", "chase"},
        "charm": {"charm", "charms", "amulet", "talisman", "pendant", "sigil", "token",
                  "artifact", "trinket", "ward"},
        "power": {"power", "powers", "powerful", "might", "magic", "magical", "magics",
                  "strength", "force", "forces", "abilities", "ability"},
        "voice": {"voice", "voices", "voice's", "words", "speech", "manner", "presence"},
        "demeanor": {"demeanor", "demeanour", "manner", "bearing", "presence", "aura",
                     "appearance", "look"},
        "terrify": {"terrify", "terrifies", "terrified", "terrifying", "terrifyingly",
                    "frighten", "frightens", "frightened", "frightening", "fear",
                    "fears", "feared", "fearful", "afraid", "horror", "horrified",
                    "horrifying", "dread", "dreaded", "panic", "panicked", "terror",
                    "terrors", "cowering", "quail", "shudder"},
        "mortals": {"mortals", "mortal", "humans", "human", "people", "villagers",
                    "folk", "peasants", "townsfolk"},
        "emotional": {"emotional", "emotion", "emotions", "feelings", "feeling",
                      "heart", "anger", "rage", "grief", "despair", "joy", "fury",
                      "outburst", "outbursts", "temper"},
        "outburst": {"outburst", "outbursts", "eruption", "surge", "surges", "surged",
                     "explosion", "explodes", "exploding", "flare", "flared"},
        "world": {"world", "worlds", "realm", "realms", "land", "plane", "earth"},
        "rule": {"rule", "rules", "ruled", "ruling", "reign", "reigns", "reigned",
                 "reigning", "govern", "governs", "governing", "command", "commands",
                 "commanding", "control", "controls", "controlling", "led", "leads",
                 "leading"},
        "nightmare": {"nightmare", "nightmares", "nightmarish", "dream", "dreams",
                      "dreaming", "dreamscape"},
        "blessing": {"blessing", "blessings", "blessed", "boon", "gift", "gifts",
                     "favor", "favour", "grant"},
        "curse": {"curse", "curses", "cursed", "cursing", "affliction", "blight",
                  "hex", "hexed"},
        "disease": {"disease", "diseases", "sick", "sickness", "illness", "plague",
                    "infection", "ailment", "affliction", "fever"},
        "divine": {"divine", "divinely", "holy", "sacred", "god", "gods", "goddess",
                   "godly", "celestial", "seraph", "seraphim", "angels", "heavenly"},
        "goddess": {"goddess", "goddesses", "queen", "deity", "deities", "divinity",
                    "nightmare", "spirit"},
        "fears": {"fears", "fear", "nightmares", "terrors", "dreads", "horrors",
                  "anxieties", "dreams"},
        "harvest": {"harvest", "harvests", "harvested", "harvesting", "collect",
                    "collects", "collected", "collecting", "gather", "gathers",
                    "gathered", "gathering", "reap", "reaps", "reaped"},
        "queen": {"queen", "queen's", "ruler", "monarch", "sovereign", "empress",
                  "ladyship"},
        "compact": {"compact", "shadow", "shadows", "cult", "sect", "order", "faction",
                    "brotherhood", "coven"},
        "friend": {"friend", "friends", "friendship", "ally", "allies", "companion",
                   "companions", "confidant", "guide"},
    }
    
    for fact in facts:
        # Try to find capitalized words (proper nouns)
        proper_nouns = re.findall(r'\b[A-Z][a-z]+\b', fact)
        proper_nouns = [w for w in proper_nouns if w.lower() not in stop_words]
        
        found = False
        # Expand every key content word (proper nouns AND common words) with
        # its synonym family, so paraphrases of the beat still match.
        search_terms = set()
        if proper_nouns:
            for pn in proper_nouns:
                search_terms.add(pn.lower())
                search_terms.update(synonym_map.get(pn.lower(), set()))
        words = re.findall(r'\b[a-zA-Z]{4,}\b', fact)
        for w in words:
            wl = w.lower()
            if wl in stop_words:
                continue
            search_terms.add(wl)
            search_terms.update(synonym_map.get(wl, set()))
        
        if search_terms:
            for term in search_terms:
                if term in text_lower:
                    found = True
                    break
        
        if not found:
            failed_facts.append(fact)
            
    return failed_facts


def evaluate_chapter(chapter_num):
    layers = load_layer_files()
    chapter_text = load_chapter(chapter_num)
    if not chapter_text.strip():
        return {"error": f"Chapter {chapter_num} is empty or missing",
                "overall_score": 0.0}

    # Extract this chapter's outline entry — scoped to the DETAILED section so the
    # HIGH-LEVEL ROADMAP one-liner is never judged as the real beats entry.
    outline = layers["outline"]
    if "## DETAILED CHAPTER OUTLINES" in outline:
        outline = outline.split("## DETAILED CHAPTER OUTLINES", 1)[1]
    ch_pattern = rf'###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*{chapter_num}\b.*?(?=###\s*\*?\*?\s*(?:Chapter|Ch\.?)\s*\*?\*?\s*(?:\d+)\b|## Act|## Foreshadowing|$)'
    ch_match = re.search(ch_pattern, outline, re.IGNORECASE | re.DOTALL)
    if not ch_match:
        raise ValueError(
            f"Chapter {chapter_num} outline entry not found in the "
            f"## DETAILED CHAPTER OUTLINES section — cannot evaluate against an empty outline."
        )
    chapter_outline = ch_match.group(0)

    # Load previous chapter tail (600-word, sentence-boundary trimmed)
    prev_text = load_chapter(chapter_num - 1) if chapter_num > 1 else "(first chapter)"
    prev_tail = textstats.tail_context(prev_text, max_words=600) if chapter_num > 1 else prev_text

    # Reader-knowledge view: public foundation + core + prior As-of. Sealed facts withheld.
    canon_view = canon_mod.judge_view_md(canon_mod.parse_canon(layers["canon"]), chapter_num)

    # Check for active narrative debts to resolve in this chapter
    chapter_harvests = re.findall(r'\[Harvest:\s*([a-zA-Z0-9_-]+)', chapter_outline, re.IGNORECASE)
    active_debts_to_resolve = []
    if chapter_harvests:
        try:
            state_path = paths.get_project_dir() / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            debts = state.get("debts", [])
            for h_slug in chapter_harvests:
                h_slug_clean = h_slug.strip().lower()
                for d in debts:
                    if h_slug_clean in d.lower():
                        active_debts_to_resolve.append(d)
        except Exception:
            pass

    prompt = build_chapter_prompt(
        voice=layers["voice"],
        world=layers["world"][:4000],  # truncate world bible
        characters=layers["characters"],
        canon_view=canon_view,
        chapter_outline=chapter_outline,
        prev_chapter_tail=prev_tail,
        chapter_text=chapter_text,
        debt_warnings=active_debts_to_resolve,
    )
    result = call_judge_json(prompt, max_tokens=8000, model=validation.ScoreOutput)

    # Mechanical slop check -- adjusts score independently of judge
    slop = slop_score(chapter_text)
    result["slop"] = slop
    if "overall_score" in result:
        adjusted = max(0, result["overall_score"] - slop["slop_penalty"])
        
        # Word count penalty
        genre_cfg = load_genre()
        estimated_words = genre_cfg["generation"]["outline"]["estimated_words"]
        chapter_count = genre_cfg["generation"]["outline"]["estimated_chapters"]
        target_words = estimated_words // chapter_count
        actual_words = len(chapter_text.split())
        
        # Word count penalty with climax/finale buffer
        length_penalty = 0.0
        tolerance_min = int(target_words * 0.8)
        
        is_climax = False
        if chapter_num == chapter_count:
            is_climax = True
        else:
            if "climax" in chapter_outline.lower() or "battle" in chapter_outline.lower() or "final" in chapter_outline.lower() or "coup" in chapter_outline.lower():
                is_climax = True
                
        if is_climax:
            tolerance_max = int(target_words * 1.55) # ~5,000 words ceiling
            print(f"  [INFO] Climax chapter detected: higher length ceiling allowed ({tolerance_max} words)", file=sys.stderr)
        else:
            tolerance_max = int(target_words * 1.25) # 4,000 words ceiling
            
        if actual_words < tolerance_min:
            length_penalty = max(0, (1 - actual_words / tolerance_min)) * 3.0
            adjusted = max(0, adjusted - length_penalty)
        elif actual_words > tolerance_max:
            length_penalty = max(0, (actual_words / tolerance_max - 1)) * 3.0
            adjusted = max(0, adjusted - length_penalty)
            
        # Orientation facts check
        failed_facts = check_orientation_facts(chapter_text, chapter_outline)
        orientation_penalty = 0.0
        if len(failed_facts) >= 2:
            orientation_penalty = min(len(failed_facts) * 1.0, 2.0)
            adjusted = max(0, adjusted - orientation_penalty)
            print(f"  [ORIENTATION] FAILED: {len(failed_facts)} fact(s) not dramatized: {failed_facts} — penalty: -{orientation_penalty:.2f}", file=sys.stderr)
            result["orientation_failed_facts"] = failed_facts
            
        print(f"  [LENGTH] {actual_words}/{target_words} words — penalty: -{length_penalty:.2f}", file=sys.stderr)
        result["length_penalty"] = length_penalty
        result["orientation_penalty"] = orientation_penalty
        result["raw_judge_score"] = result["overall_score"]
        result["overall_score"] = round(adjusted, 2)

    return result


# --- Full Novel Evaluation ---

FULL_NOVEL_PROMPT = paths.load_prompt("evaluate_full_novel")


def evaluate_full():
    layers = load_layer_files()
    chapters = load_all_chapters()

    if not chapters:
        return {"error": "No chapters found", "novel_score": 0.0}

    # Build chapter metadata (word count + per-chapter score)
    metadata = []
    for num in sorted(chapters.keys()):
        text = chapters[num]
        word_count = len(text.split())
        ch_score = _latest_chapter_score(num)
        score_line = f"Score: {ch_score}/10" if ch_score is not None else "Score: (not yet evaluated)"
        title = ""
        first_line = text.strip().split('\n')[0] if text.strip() else ""
        if first_line.startswith("#"):
            title = f" - Title: {first_line.lstrip('#').strip()}"
        metadata.append(
            f"Chapter {num}{title} ({word_count} words):\n"
            f"  {score_line}"
        )

    prompt = FULL_NOVEL_PROMPT.format(
        voice=layers["voice"],
        world_summary=layers["world"][:3000],
        characters=layers["characters"],
        outline=layers["outline"],
        chapter_summaries="\n".join(metadata),
    )

    result = call_judge_json(prompt, model=validation.NovelScoreOutput)

    # Apply mechanical slop penalty across the full manuscript text
    full_text = "\n\n".join(chapters.get(i, "") for i in sorted(chapters.keys()))
    slop = slop_score(full_text)
    result["full_slop"] = slop
    if "novel_score" in result:
        adjusted = max(0, result["novel_score"] - slop["slop_penalty"])
        result["raw_novel_score"] = result["novel_score"]
        result["novel_score"] = round(adjusted, 2)
        result["slop_penalty_applied"] = slop["slop_penalty"]

    return result


def _latest_chapter_score(ch_num: int) -> float | None:
    """Look up the most recent per-chapter eval score for chapter N from eval logs."""
    eval_log_dir = paths.get_eval_logs_dir()
    pattern = f"*_ch{ch_num:02d}.json"
    matches = sorted(eval_log_dir.glob(pattern))
    if not matches:
        return None
    try:
        data = json.loads(matches[-1].read_text(encoding="utf-8"))
        return data.get("overall_score")
    except (json.JSONDecodeError, OSError, KeyError):
        return None


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Evaluate the novel")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--phase", choices=["foundation"],
                       help="Evaluate planning documents")
    group.add_argument("--chapter", type=int,
                       help="Evaluate a specific chapter number")
    group.add_argument("--full", action="store_true",
                       help="Evaluate the entire novel")
    parser.add_argument("--project", default=None, help="Project name (under projects/)")
    args = parser.parse_args()

    if args.project:
        paths.set_project_name(args.project)

    if args.phase == "foundation":
        result = evaluate_foundation()
        score_key = "overall_score"
    elif args.chapter is not None:
        result = evaluate_chapter(args.chapter)
        score_key = "overall_score"
    elif args.full:
        result = evaluate_full()
        score_key = "novel_score"

    # Print structured output
    print("---")
    if score_key in result:
        print(f"{score_key}: {result[score_key]}")
    for key, val in result.items():
        if key == score_key:
            continue
        if isinstance(val, dict):
            print(f"{key}: {val.get('score', 'N/A')} -- {val.get('note', '')}")
        else:
            print(f"{key}: {val}")

    # Save full eval log
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = args.phase or (f"ch{args.chapter:02d}" if args.chapter else "full")
    eval_log_dir = paths.get_eval_logs_dir()  # also creates the directory
    log_path = eval_log_dir / f"{timestamp}_{mode}.json"
    paths.save_json_atomic(result, log_path)
    print(f"\neval_log: {log_path}")


if __name__ == "__main__":
    main()
