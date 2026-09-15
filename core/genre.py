"""
genre.py — Genre configuration loader.
All pipeline scripts call load_genre() to get active genre config.
Configs are per-project in projects/{name}/active_genre.json.
"""
from core import paths
import json
import math
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ACTIVE_PATH = BASE_DIR / "active_genre.json"

_cache = None

REQUIRED_KEYS = [
    "genre_name", "identity", "generation", "evaluation", "framework",
    "identity.seed_system", "identity.world_system", "identity.character_system",
    "identity.outline_system", "identity.chapter_system", "identity.revision_system",
    "identity.canon_system", "identity.evaluator_system",
    "generation.world", "generation.world.description", "generation.world.sections",
    "generation.character", "generation.character.description", "generation.character.focus_areas",
    "generation.outline", "generation.outline.description",
    "generation.outline.estimated_chapters", "generation.outline.estimated_words",
    "generation.outline.notes",
    "generation.seed_generate_prompt", "generation.seed_riff_prompt",
    "generation.gen_world_prompt", "generation.gen_characters_prompt",
    "generation.gen_outline_prompt", "generation.gen_outline_part2_prompt",
    "generation.gen_canon_prompt",
    "generation.draft_chapter_instructions", "generation.anti_pattern_rules",
    "generation.canon_categories", "generation.arc_summary_premise",
    "evaluation.foundation", "evaluation.foundation.overall_calibration",
    "evaluation.foundation.dimensions",
    "evaluation.chapter", "evaluation.chapter.overall_calibration",
    "evaluation.chapter.dimensions",
    "evaluation.reader_panel", "evaluation.reader_panel.genre_reader_identity",
    "framework.lore_priorities", "framework.character_framework", "framework.plot_framework",
    "framework.disclosure_framework",
    "framework.premise_arc", "framework.premise_arc_beats",
]

FOUNDATION_DIM_KEYS = {"world_depth", "character_depth", "plot_structure",
                        "internal_consistency", "voice_clarity", "canon_coverage"}
CHAPTER_DIM_KEYS = {"voice_adherence", "beat_coverage", "character_voice",
                     "prose_quality", "engagement", "continuity",
                     "reader_grounding"}

def _get_nested(d, key):
    """Get nested key like 'identity.seed_system' from dict."""
    parts = key.split(".")
    for part in parts:
        if isinstance(d, dict) and part in d:
            d = d[part]
        else:
            return None
    return d

def validate(config):
    """Raise ValueError with all validation errors at once."""
    errors = []
    
    # Check all required keys exist
    for key in REQUIRED_KEYS:
        val = _get_nested(config, key)
        if val is None:
            errors.append(f"Missing required key: {key}")
    
    # Check generation prompt strings are not empty
    prompt_keys = [
        "generation.seed_generate_prompt", "generation.seed_riff_prompt",
        "generation.gen_world_prompt", "generation.gen_characters_prompt",
        "generation.gen_outline_prompt", "generation.gen_outline_part2_prompt",
        "generation.gen_canon_prompt",
    ]
    for key in prompt_keys:
        val = _get_nested(config, key)
        if val and len(str(val).strip()) < 50:
            errors.append(f"Prompt too short ({len(str(val).strip())} chars): {key}")
    
    # Check foundation dimension keys
    fdims = _get_nested(config, "evaluation.foundation.dimensions") or []
    fdims_keys = {d.get("key") for d in fdims}
    missing_f = FOUNDATION_DIM_KEYS - fdims_keys
    if missing_f:
        errors.append(f"Foundation dims missing: {missing_f}")
    extra_f = fdims_keys - FOUNDATION_DIM_KEYS
    if extra_f:
        errors.append(f"Foundation dims extra unknown keys: {extra_f}")
    
    # Check chapter dimension keys
    cdims = _get_nested(config, "evaluation.chapter.dimensions") or []
    cdims_keys = {d.get("key") for d in cdims}
    missing_c = CHAPTER_DIM_KEYS - cdims_keys
    if missing_c:
        errors.append(f"Chapter dims missing: {missing_c}")
    extra_c = cdims_keys - CHAPTER_DIM_KEYS
    if extra_c:
        errors.append(f"Chapter dims extra unknown keys: {extra_c}")
    
    # Validate foundation weights sum to ~1.0
    fweight = sum(d.get("weight", 0) for d in fdims)
    if not math.isclose(fweight, 1.0, abs_tol=0.02):
        errors.append(f"Foundation weights sum to {fweight:.3f}, expected ~1.0")
    
    # Validate chapter weights sum to ~1.0
    cweight = sum(d.get("weight", 0) for d in cdims)
    if not math.isclose(cweight, 1.0, abs_tol=0.02):
        errors.append(f"Chapter weights sum to {cweight:.3f}, expected ~1.0")
    
    # Check criteria strings are substantial
    for dim in fdims + cdims:
        crit = dim.get("criteria", "")
        if len(crit.strip()) < 30:
            errors.append(f"Criteria too short for dim '{dim.get('key')}': {len(crit.strip())} chars")
    
    # Check evaluator_system starts correctly
    eval_sys = _get_nested(config, "identity.evaluator_system") or ""
    if not eval_sys.startswith("You are a literary critic"):
        errors.append("evaluator_system must start with 'You are a literary critic...'")
    
    # Check framework fields
    for fkey in ["lore_priorities", "character_framework", "plot_framework", "disclosure_framework", "premise_arc"]:
        val = _get_nested(config, f"framework.{fkey}")
        if val and len(str(val).strip()) < 20:
            errors.append(f"framework.{fkey} too short ({len(str(val).strip())} chars)")
    
    pbeats = _get_nested(config, "framework.premise_arc_beats")
    if not isinstance(pbeats, list) or len(pbeats) < 3 or len(pbeats) > 6:
        errors.append(f"framework.premise_arc_beats must be a list of 3-6 beat labels, got: {type(pbeats).__name__} ({len(pbeats) if isinstance(pbeats, list) else 'N/A'})")
    
    if errors:
        raise ValueError("Genre config validation failed:\n  " + "\n  ".join(errors))


def load_genre():
    global _cache
    if _cache is not None:
        return _cache

    project_active_path = paths.get_active_genre_path()
    
    if project_active_path.exists():
        path = project_active_path
    elif ACTIVE_PATH.exists():
        path = ACTIVE_PATH
    else:
        raise FileNotFoundError(
            "No genre config found. "
            f"Checked:\n"
            f"  1. {project_active_path} (project active_genre.json)\n"
            f"  2. {ACTIVE_PATH} (root active_genre.json)\n\n"
            "Run gen_genre_framework.py or set --project."
        )
    
    if not path.exists():
        raise FileNotFoundError(f"Genre config not found: {path}")
    
    config = json.loads(path.read_text(encoding="utf-8"))
    validate(config)
    _cache = config
    return config


def reload_genre():
    global _cache
    _cache = None
    return load_genre()


def format_prompt(template, **kwargs):
    """Safely format a prompt template with kwargs, preserving unset placeholders."""
    import string
    # Only replace placeholders that are provided
    formatter = string.Formatter()
    result = template
    for key, val in kwargs.items():
        result = result.replace(f"{{{key}}}", str(val))
    return result


PROSE_MODES = ("first_intimate", "first_voicey", "third_close", "third_scene")
_PROSE_DIR = BASE_DIR.parent / "fuel" / "prose"


def load_prose_pack(mode: str) -> str:
    """Return fuel/prose/<mode>.md, or empty string if unset/missing."""
    if not mode:
        return ""
    p = _PROSE_DIR / f"{mode}.md"
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")


def prose_mode_system_block(genre_cfg: dict) -> str:
    """System-prompt fragment for the project's prose mode, if any."""
    mode = (genre_cfg or {}).get("prose_mode") or ""
    pack = load_prose_pack(mode)
    if not pack:
        return ""
    return f"\n\n=== PROSE MODE ({mode}) ===\n{pack}\n"


# CLI usage: python genre.py --validate
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true", help="Validate current genre config")
    args = parser.parse_args()
    if args.validate:
        try:
            cfg = load_genre()
            print(f"✅ Genre '{cfg['genre_name']}' loaded and valid")
            print(f"   Estimated: {cfg['generation']['outline']['estimated_chapters']} chapters, {cfg['generation']['outline']['estimated_words']:,} words")
            print(f"   Foundation dims: {[d['key'] for d in cfg['evaluation']['foundation']['dimensions']]}")
            print(f"   Chapter dims: {[d['key'] for d in cfg['evaluation']['chapter']['dimensions']]}")
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
            print(f"❌ Validation failed: {e}")
            sys.exit(1)
