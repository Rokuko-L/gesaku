#!/usr/bin/env python3
"""
gen_novel_tex.py — Generate a custom novel.tex via LLM.
Calls the writer model with project context, outputs a designed LaTeX template.

Usage: uv run python gen_novel_tex.py
       uv run python gen_novel_tex.py --project brothersister
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm
from core import paths
from core.paths import get_novel_title
import re
import sys
import json
import subprocess
from pathlib import Path
from dotenv import load_dotenv


load_dotenv()

ROOT = Path(__file__).resolve().parent


def load_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def extract_thematic_core(seed_text: str) -> str:
    """Extract the ## Thematic Core section from seed.txt."""
    m = re.search(
        r"##\s*Thematic\s*Core\s*\n\n(.*?)(?:\n\n##\s|\Z)",
        seed_text, re.DOTALL | re.IGNORECASE
    )
    if m:
        return m.group(1).strip()
    # fallback: look for any standalone paragraph that reads like a theme
    lines = [l.strip() for l in seed_text.split('\n') if l.strip()]
    for line in lines:
        if line.startswith('"') and line.count('.') > 1:
            return line
    return ""


def extract_title(seed_text: str, state: dict) -> str:
    """Extract title from seed.txt first line or state.json."""
    title = state.get("title", "")
    if title and title.lower() not in ("a novel", "the novel", "untitled"):
        return title
    first_line = seed_text.strip().split('\n')[0] if seed_text.strip() else ""
    if first_line.startswith("#"):
        return first_line.lstrip("#").strip()
    return paths.get_project_name().replace("_", " ").title()


def get_author() -> str:
    """Get author name from git config."""
    try:
        return subprocess.check_output(
            ["git", "config", "user.name"], text=True
        ).strip()
    except Exception:
        return "Author Name"


def get_genre_summary(genre_cfg: dict) -> str:
    """Extract a readable summary of the genre configuration."""
    parts = []
    name = genre_cfg.get("genre_name", "Unknown")
    parts.append(f"Genre: {name}")
    identity = genre_cfg.get("identity", {})
    for key in ("chapter_system", "world_system"):
        val = identity.get(key, "")
        if val:
            parts.append(f"{key}: {val[:200]}")
    framework = genre_cfg.get("framework", {})
    for key in ("lore_priorities",):
        val = framework.get(key, "")
        if val:
            parts.append(f"{key}: {val[:200]}")
    user_notes = genre_cfg.get("user_directives", "")
    if user_notes:
        parts.append(f"User notes: {user_notes[:300]}")
    return "\n\n".join(parts)


def extract_voice_part2(voice_text: str) -> str:
    """Extract everything after the Part 2 header."""
    idx = voice_text.find("## Part 2:")
    if idx == -1:
        idx = voice_text.find("## Part 2 ")
    if idx == -1:
        return ""
    return voice_text[idx:]


def build_prompt(
    title: str,
    author: str,
    genre_summary: str,
    voice_part2: str,
    thematic_core: str,
    seed_text: str,
    char_text: str,
    world_text: str,
    voice_text: str,
) -> str:
    """Build the full prompt for the LLM."""

    parts = []

    # Title block
    parts.append(f"TITLE: {title}")
    parts.append(f"AUTHOR: {author}")

    # Genre block
    parts.append(f"\nGENRE CONFIGURATION:\n{genre_summary}")

    # Voice block
    if voice_part2.strip() and "<!-- Generated" not in voice_part2:
        parts.append(f"\nVOICE IDENTITY (novel's tone and POV):\n{voice_part2[:1500]}")
    else:
        parts.append(f"\nVOICE IDENTITY:\n(No voice identity filled yet for this novel.)")

    # Thematic core
    if thematic_core:
        parts.append(f"\nTHEMATIC CORE (use for epigraph and design inspiration):\n{thematic_core}")

    # World flavor (brief)
    if world_text:
        short_world = world_text[:800]
        parts.append(f"\nWORLD FLAVOR:\n{short_world}")

    # Characters (brief)
    if char_text:
        short_char = char_text[:600]
        parts.append(f"\nCHARACTERS:\n{short_char}")

    # Seed premise
    if seed_text:
        short_seed = seed_text[:1000]
        parts.append(f"\nSTORY PREMISE:\n{short_seed}")

    return "\n\n".join(parts)


SYSTEM_PROMPT = paths.load_prompt("gen_novel_tex_system")

PROMPT_TEMPLATE = paths.load_prompt("gen_novel_tex_template")


def main():
    # Parse optional --project flag
    if "--project" in sys.argv:
        idx = sys.argv.index("--project")
        if idx + 1 < len(sys.argv):
            project_name = sys.argv[idx + 1]
            paths.set_project_name(project_name)

    # Load project context
    seed_text = load_file(paths.get_seed_path())
    voice_text = load_file(paths.get_voice_path())
    world_text = load_file(paths.get_world_path())
    char_text = load_file(paths.get_characters_path())

    state = {}
    try:
        state = json.loads(load_file(paths.get_state_path()))
    except (json.JSONDecodeError, ValueError):
        print("ERROR: state.json is corrupted or unparseable — the title page may get a wrong title.", file=sys.stderr)

    genre_cfg = {}
    try:
        genre_cfg = json.loads(load_file(paths.get_active_genre_path()))
    except (json.JSONDecodeError, ValueError):
        print("ERROR: active_genre.json is corrupted or unparseable — genre context will be empty.", file=sys.stderr)

    title = extract_title(seed_text, state)
    author = get_author()
    genre_summary = get_genre_summary(genre_cfg)
    voice_part2 = extract_voice_part2(voice_text)
    thematic_core = extract_thematic_core(seed_text)

    context = build_prompt(
        title=title,
        author=author,
        genre_summary=genre_summary,
        voice_part2=voice_part2,
        thematic_core=thematic_core,
        seed_text=seed_text,
        char_text=char_text,
        world_text=world_text,
        voice_text=voice_text,
    )

    prompt = PROMPT_TEMPLATE.format(context=context)

    print(f"Generating novel.tex for: {title}", file=sys.stderr)
    print(f"  Author: {author}", file=sys.stderr)
    print(f"  Context size: {len(context)} chars", file=sys.stderr)

    # Write to typeset directory
    typeset_dir = paths.get_typeset_dir()
    dest = typeset_dir / "novel.tex"

    # Call LLM
    raw = call_llm(
        prompt=prompt,
        system=SYSTEM_PROMPT,
        model_key="writer",
        max_tokens=16000,
        temperature=0.7,
        timeout_role="standard",
    )

    # Extract LaTeX from code fences
    latex = raw.strip()
    m = re.search(r"```(?:latex|tex)?\s*\n(.*?)```", raw, re.DOTALL)
    if m:
        latex = m.group(1).strip()
    else:
        # Try to find \documentclass as anchor
        m2 = re.search(r"(\x5cdocumentclass.*?\x5cend\{document\})", raw, re.DOTALL)
        if m2:
            latex = m2.group(1).strip()

    if not latex or len(latex) < 200:
        print(f"ERROR: LLM returned invalid LaTeX (fence extraction produced {len(latex)} chars)", file=sys.stderr)
        sys.exit(1)

    if "\\end{document}" not in latex:
        print("ERROR: LLM returned INCOMPLETE LaTeX (no \\end{document}) — refusing to write a truncated novel.tex", file=sys.stderr)
        sys.exit(1)

    dest.write_text(latex, encoding="utf-8")
    print(f"Wrote novel.tex ({len(latex)} bytes) to {dest}", file=sys.stderr)


if __name__ == "__main__":
    main()
