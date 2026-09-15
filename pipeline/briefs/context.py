"""Shared loaders and text helpers for revision briefs."""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import json
import re
from pathlib import Path

from core import paths



def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def chapter_path(ch: int) -> Path:
    return paths.get_chapters_dir() / f"ch_{ch:02d}.md"


def chapter_text(ch: int) -> str:
    p = chapter_path(ch)
    if not p.exists():
        raise FileNotFoundError(f"chapter file not found: {p}")
    return p.read_text(encoding="utf-8")


def chapter_title(text: str) -> str:
    """Extract the chapter title from the first line of the md file."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            # strip leading hashes and any "Chapter N/One/Two/..." prefix
            title = re.sub(r"^#+\s*", "", line)
            title = re.sub(
                r"^Chapter\s+(?:\d+|[A-Z][a-z]+(?:-[A-Z][a-z]+)*)\s*[:—–-]*\s*",
                "", title, flags=re.I
            )
            return title.strip() if title.strip() else "Untitled"
    return "Untitled"


def word_count(text: str) -> int:
    return len(text.split())


def extract_voice_rules() -> list[str]:
    """Pull the key guardrail / voice rules from voice.md Part 1 + Part 2."""
    voice_path = paths.get_voice_path()
    if not voice_path.exists():
        return ["(voice.md not found)"]
    voice = voice_path.read_text(encoding="utf-8")

    rules: list[str] = []

    # Part 2 identity rules we always want
    rules.append("Body-first emotion (jaw, ribs, tongue before naming the feeling)")
    rules.append("No telling after showing")
    rules.append("No triadic sensory lists")
    rules.append("70%+ in-scene (dialogue and action, not summary)")
    rules.append("Dialogue: clipped, subtext-heavy, 'said' default, no adverb tags")
    rules.append("Sentence rhythm: mixed meter, fragments for pain, long for perception")
    rules.append("Vocabulary from craft/trade/body wells — no generic fantasy diction")

    # Part 1 structural slop
    rules.append("No paragraph-template-machine (vary structure)")
    rules.append("Max 1-2 em dashes per page")
    rules.append(
        """No staccato emphasis fragments. Do not follow a full sentence or clause with one or more short fragments (roughly 1-4 words, lacking a main verb or a complete subject-verb pair) used to land emphasis through rhythm and white space rather than through content. This applies regardless of how many fragments are used, their part of speech, or whether they're nouns, adjectives, or clipped clauses — the tell is the shape (statement, then staccato beat(s)), not the specific words.
Banned shape, with examples spanning different lengths and word types so the pattern is clear — do not treat these as the exhaustive list, treat them as the same failure mode wearing different clothes:

Single-word pairs: "Silver. Victory." / "Restless. Hungry."
Adjective contrasts: "Warm. Safe." / "Practical. Unadorned."
Three-part noun/adjective runs: "Stronger. Tighter. More efficient."
Clipped parallel clauses: "It hurt. It was real." / "The silence stretched. Cracked. Broke."
Negated fragments: "Not reduced. Not restructured."

If a moment needs emphatic weight, build it into the rhythm of a single full sentence (varied clause length, a strong verb, a turn in the syntax) rather than trailing fragments after it. Before finalizing any sentence that ends in a period followed by a 1-4 word fragment, ask: could this be one sentence instead? If yes, rewrite it as one."""
    )

    return rules


def analyze_writing_sample(sample_text: str) -> list[str]:
    """
    Analyze a user-supplied writing sample to calibrate voice rules.
    Measures sentence rhythm, verb choice, and dialogue patterns
    to generate personalized rules that supplement the defaults.

    Returns a list of rule strings (empty if sample is too short).
    """
    words = sample_text.split()
    if len(words) < 50:
        return []

    rules: list[str] = []

    # Sentence rhythm profile
    sentences = [s.strip() for s in re.split(r'[.!?]+', sample_text) if len(s.strip().split()) > 2]
    if len(sentences) > 3:
        lengths = [len(s.split()) for s in sentences]
        mean_len = sum(lengths) / len(lengths)
        cv = (sum((l - mean_len) ** 2 for l in lengths) / len(lengths)) ** 0.5 / mean_len if mean_len > 0 else 0
        rhythm_note = "highly varied, maintain that" if cv > 0.6 else "moderately varied, aim for more mixing" if cv > 0.4 else "too uniform, introduce more short/long contrast"
        rules.append(f"Calibrated rhythm: sentence length CV {cv:.2f} ({rhythm_note})")

    # Verb profile — check for copula avoidance
    copula_avoid = re.findall(r'\b(?:serves as|serves to|stands as|acts as|functions as|features|boasts)\b', sample_text, re.IGNORECASE)
    if len(copula_avoid) > len(words) * 0.005:  # more than 0.5% of words
        rules.append("Calibrated verb style: sample avoids copula-avoidance verbs (serves as, features, boasts) naturally — keep doing this")

    # Dialogue profile — tag preferences
    said_tags = len(re.findall(r'\bsaid\b', sample_text, re.IGNORECASE))
    fancy_tags = len(re.findall(r'\b(snarled|breathed|hissed|growled|purred|spat|huffed|scoffed|retorted|whispered|murmured|muttered|drawled|rasped|croaked)\b', sample_text, re.IGNORECASE))
    if said_tags + fancy_tags > 3:
        ratio = fancy_tags / (said_tags + fancy_tags)
        if ratio < 0.2:
            rules.append("Calibrated dialogue: sample uses 'said' almost exclusively — maintain this restraint")
        elif ratio > 0.5:
            pct = f"{ratio:.0%}"
            rules.append(f"Calibrated dialogue: sample favors fancy tags ({pct} non-'said') — consider reducing if characters sound samey")

    # Fragment frequency
    all_sents = [s.strip() for s in re.split(r'[.!?]+', sample_text) if s.strip()]
    fragments = sum(1 for s in all_sents if len(s.split()) <= 4)
    frag_pct = fragments / len(all_sents) if all_sents else 0
    if frag_pct > 0.15:
        fp = f"{frag_pct:.0%}"
        rules.append(f"Calibrated fragment usage: sample uses {fp} sentence fragments — good for punch, don't overdo it")
    elif frag_pct < 0.03 and len(all_sents) > 20:
        fp = f"{frag_pct:.0%}"
        rules.append(f"Calibrated fragment usage: sample uses only {fp} fragments — consider adding 1-2 short punch sentences per page")

    return rules


def latest_full_eval() -> Path | None:
    """Find the most recent *_full.json in eval_logs/."""
    eval_logs_dir = paths.get_eval_logs_dir()
    if not eval_logs_dir.exists():
        return None
    fulls = sorted(eval_logs_dir.glob("*_full.json"))
    return fulls[-1] if fulls else None


def latest_chapter_eval(ch: int) -> Path | None:
    """Find the most recent per-chapter eval for ch N."""
    eval_logs_dir = paths.get_eval_logs_dir()
    if not eval_logs_dir.exists():
        return None
    pattern = f"*_ch{ch:02d}.json"
    matches = sorted(eval_logs_dir.glob(pattern))
    # Also try without zero-pad
    matches += sorted(eval_logs_dir.glob(f"*_ch{ch}.json"))
    matches = sorted(set(matches))
    return matches[-1] if matches else None


def load_panel() -> dict | None:
    p = paths.get_edit_logs_dir() / "reader_panel.json"
    if not p.exists():
        return None
    return load_json(p)


def load_cuts(ch: int) -> dict | None:
    p = paths.get_edit_logs_dir() / f"ch{ch:02d}_cuts.json"
    if not p.exists():
        return None
    return load_json(p)


# ---------------------------------------------------------------------------
# panel feedback extraction
# ---------------------------------------------------------------------------


def panel_mentions_for_chapter(panel: dict, ch: int) -> dict:
    """Extract all reader comments that mention this chapter."""
    readers = panel.get("readers", {})
    disagreements = panel.get("disagreements", [])

    mentions: dict[str, list[str]] = {
        "momentum_loss": [],
        "worst_scene": [],
        "cut_candidate": [],
        "best_scene": [],
        "thinnest_character": [],
        "missing_scene": [],
        "earned_ending": [],
    }

    # Use word-boundary regex so "Chapter 2" doesn't match "Chapter 21"
    ch_re = re.compile(
        rf"\b(?:Chapter|Ch\.?)\s*{ch}\b", re.I
    )

    for reader_name, reader_data in readers.items():
        for key in mentions:
            val = reader_data.get(key, "")
            if isinstance(val, (dict, list)):
                def extract_strings(v):
                    if isinstance(v, dict):
                        return [s for x in v.values() for s in extract_strings(x)]
                    elif isinstance(v, list):
                        return [s for x in v for s in extract_strings(x)]
                    return [str(v)]
                text = " ".join(extract_strings(val))
            else:
                text = str(val)

            if ch_re.search(text):
                mentions[key].append(f"[{reader_name}] {text}")

    # Also check disagreements for this chapter
    flagged_issues: list[str] = []
    for d in disagreements:
        if d.get("chapter") == ch:
            q = d.get("question", "")
            flagged = d.get("flagged_by", [])
            count = len(flagged)
            flagged_issues.append(
                f"{q}: flagged by {count}/4 readers ({', '.join(flagged)})"
            )

    return {
        "mentions": mentions,
        "flagged_issues": flagged_issues,
    }


# ---------------------------------------------------------------------------
# brief generators
# ---------------------------------------------------------------------------
