"""Parsing and counting helpers for pipeline output.

Score parsing raises on a missing key on purpose — a silently absent score
must never reach keep/discard decisions.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import re
from pathlib import Path

from core import paths

def parse_score(stdout: str, key: str = "overall_score") -> float:
    """
    Parse a score from evaluate.py YAML-like stdout output.
    Looks for lines like 'overall_score: 8.0' or 'novel_score: 7.5'.

    Raises ValueError when the key is missing or not a float — a silently
    missing score must never flow into keep/discard decisions.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith(f"{key}:"):
            val = line.split(":", 1)[1].strip()
            try:
                return float(val)
            except ValueError:
                break
    raise ValueError(
        f"'{key}:' not found (or not a float) in evaluator output — the eval "
        f"likely crashed or changed its output format. stdout tail: {stdout[-300:]!r}"
    )


def parse_score_any(stdout: str, *keys: str) -> float:
    """Parse the first score key present in evaluate.py stdout.

    Explicit replacement for the old 'parse key A, fall back to B on -1.0'
    pattern. Raises ValueError when none of the keys parse.
    """
    for key in keys:
        try:
            return parse_score(stdout, key)
        except ValueError:
            continue
    raise ValueError(
        f"None of {keys} found in evaluator output. "
        f"stdout tail: {stdout[-300:]!r}"
    )

def parse_lore_score(stdout: str) -> float:
    """Parse lore_score from foundation evaluation output."""
    return parse_score(stdout, "lore_score")

def count_words_in_chapters() -> int:
    """Sum word count across all chapter files in the active project."""
    total = 0
    chapters_dir = paths.get_chapters_dir()
    if chapters_dir.exists():
        for f in chapters_dir.glob("ch_*.md"):
            total += len(f.read_text(encoding="utf-8").split())
    return total

def count_chapter_files() -> int:
    """Count the number of chapter files in the active project."""
    chapters_dir = paths.get_chapters_dir()
    if not chapters_dir.exists():
        return 0
    return len(list(chapters_dir.glob("ch_*.md")))

def _chapter_num_key(path) -> int:
    """Numeric sort key for ch_*.md files (ch_2 must sort before ch_10)."""
    m = re.search(r"ch_(\d+)\.md", Path(path).name)
    return int(m.group(1)) if m else 10**9
