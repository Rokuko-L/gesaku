#!/usr/bin/env python3
"""Mechanical slop detection — deterministic, no LLM.

Word lists, tic families, and the density-based penalty model that
`pipeline/evaluate.py` subtracts from the judge score. Split out so the
detector can be tuned and tested without loading the judge stack.

`PROSE_TIC_PATTERNS` / `slop_score` etc. are imported by `repair_slop.py`
and `run_drafts.py`.
"""

import re
import sys

from core import paths


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
