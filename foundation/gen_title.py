#!/usr/bin/env python3
"""
gen_title.py — Title tournament with a per-project 4-judge panel.
Each judge is a real LLM call with a distinct persona from the project config.
Scores are aggregated by average across all 4 judges.
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.llm import call_llm, parse_json_response
from core import paths
import os
import sys
import json
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from core.genre import load_genre

load_dotenv()

ROUNDS = 3
INITIAL_CANDIDATES = 15
TOP_K_SURVIVORS = 5
MUTATIONS_PER_ROUND = 10
CONTEXT_LIMIT = 3000

DEFAULT_TITLE_JUDGES = [
    {
        "key": "editor",
        "name": "The Editor",
        "persona": (
            "You are a senior fiction editor at a major publishing house. "
            "You evaluate book titles for intrigue, subtext, and whether they feel "
            "earned by the story they promise. You reject titles that are generic, "
            "pretentious, or mismatched to tone. You respond with valid JSON only."
        ),
    },
    {
        "key": "genre_reader",
        "name": "The Genre Reader",
        "persona": (
            "You are an avid genre reader who picks books by their cover and title. "
            "You know what signals belong to your favourite genres and what feels like "
            "a bait-and-switch. You value titles that promise the right experience. "
            "You respond with valid JSON only."
        ),
    },
    {
        "key": "writer",
        "name": "The Writer",
        "persona": (
            "You are a published author who reads titles the way a carpenter reads "
            "joinery. You care about originality, rhythm, memorability, and whether a "
            "title earns its space on a shelf crowded with competition. You notice when "
            "a title is doing actual work vs. decoration. You respond with valid JSON only."
        ),
    },
    {
        "key": "first_reader",
        "name": "The First Reader",
        "persona": (
            "You are a general reader browsing a bookstore. You don't analyse — you feel. "
            "A title either grabs you or it doesn't. You know what makes you pick a book "
            "up off the table and what makes you walk past. Your feedback is gut-level. "
            "You respond with valid JSON only."
        ),
    },
]


def call_writer(prompt, temp=0.7):
    return call_llm(prompt=prompt, model_key="writer", max_tokens=2000, temperature=temp, timeout_role="standard")


def call_judge(prompt, system_prompt, temp=0.1):
    return call_llm(prompt=prompt, system=system_prompt, model_key="judge", max_tokens=2000, temperature=temp, timeout_role="short")


def parse_titles_list(raw_response):
    titles = []
    lines = raw_response.strip().split("\n")
    for line in lines:
        match = re.match(r'^\d+[\.\)]\s*(.+)$', line.strip())
        if match:
            t = match.group(1).strip().strip('"').strip("'").strip("*").strip("_").strip()
            if t:
                titles.append(t)
    if not titles:
        for line in lines:
            cleaned = line.strip().strip('"').strip("'").strip("*").strip("_").strip()
            if cleaned and not cleaned.startswith("Here") and not re.match(r'^(?:15|10)\b', cleaned):
                titles.append(cleaned)
    return titles[:25]


def load_title_judges():
    cfg = load_genre()
    judges_raw = (
        cfg
        .get("evaluation", {})
        .get("reader_panel", {})
        .get("title_judges", None)
    )
    if judges_raw and isinstance(judges_raw, list) and len(judges_raw) >= 4:
        return judges_raw[:4]
    return None


def generate_judges(static_context):
    prompt = f"""{static_context}

You are a literary agent preparing a title-selection panel for a novel.
Create exactly 4 judge personas for evaluating book titles. Each persona
must have a distinct perspective on what makes a great title.

Return valid JSON — an array of 4 objects with keys: "key", "name", "persona".
Each "persona" is a 2-3 sentence description of who they are and what they
value in a book title. The keys must be: "editor", "genre_reader", "writer", "first_reader".

Example:
[
  {{
    "key": "editor",
    "name": "The Editor",
    "persona": "You are a senior fiction editor..."
  }},
  ...
]

JSON only, no markdown, no preamble."""
    raw = call_writer(prompt, temp=0.7)
    try:
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            raw_json = raw[start:end+1]
            result = json.loads(raw_json)
        else:
            result = parse_json_response(raw)
        if isinstance(result, list) and len(result) >= 4:
            return result[:4]
        print(f"WARNING: Judge generation returned {type(result).__name__}", file=sys.stderr)
    except Exception as e:
        print(f"WARNING: Judge generation failed: {e}", file=sys.stderr)
    return None


def persist_judges(judges):
    cfg = load_genre()
    reader_panel = cfg.setdefault("evaluation", {}).setdefault("reader_panel", {})
    reader_panel["title_judges"] = judges
    state_path = paths.get_state_path()
    genre_path = state_path.parent / "active_genre.json"
    if genre_path.exists():
        paths.save_json_atomic(cfg, genre_path)
        print(f"Saved title_judges to {genre_path}", file=sys.stderr)


def main():
    if "--project" in sys.argv:
        idx = sys.argv.index("--project")
        if idx + 1 < len(sys.argv):
            paths.set_project_name(sys.argv[idx + 1])

    required_files = {
        "seed.txt": paths.get_seed_path(),
        "world.md": paths.get_world_path(),
        "characters.md": paths.get_characters_path(),
    }

    for name, p in required_files.items():
        if not p.exists():
            print(f"ERROR: {name} not found at {p}", file=sys.stderr)
            sys.exit(1)

    seed = required_files["seed.txt"].read_text(encoding="utf-8")[:CONTEXT_LIMIT]
    world = required_files["world.md"].read_text(encoding="utf-8")[:CONTEXT_LIMIT]
    characters = required_files["characters.md"].read_text(encoding="utf-8")[:CONTEXT_LIMIT]

    genre_name = load_genre().get("genre_name", "Unknown")
    project_name = paths.get_project_name()

    static_context = f"""<context>
<genre>{genre_name}</genre>
<seed>{seed}</seed>
<world_bible>{world}</world_bible>
<characters>{characters}</characters>
</context>"""

    # --- Load or generate 4 title judges ---
    judges = load_title_judges()
    if judges is None:
        print("No title_judges in project config. Generating 4 from context...", file=sys.stderr)
        judges = generate_judges(static_context)
        if judges is None:
            print("Falling back to default title judges.", file=sys.stderr)
            judges = DEFAULT_TITLE_JUDGES
        else:
            persist_judges(judges)

    print(f"Title Tournament for '{project_name}' ({genre_name}) — {len(judges)} judges", file=sys.stderr)
    for j in judges:
        print(f"  🧑‍⚖️  {j['name']} ({j['key']})", file=sys.stderr)

    # --- Phase 1: Initial Generation ---
    writer_prompt = f"""{static_context}

You are a creative book naming assistant. Based on the provided context,
brainstorm {INITIAL_CANDIDATES} unique, high-impact, and thematic book titles.
Avoid generic options like "The Novel" or "Untitled".
Return ONLY a numbered list.
Example:
1. Title One
2. Title Two"""

    print("Generating initial title candidates...", file=sys.stderr)
    raw_titles = call_writer(writer_prompt, temp=0.7)
    current_candidates = parse_titles_list(raw_titles)
    print(f"Generated {len(current_candidates)} candidates.", file=sys.stderr)

    # Hall of Fame: title -> {"scores": [4 judge scores], "avg": float}
    hall_of_fame = {}

    # --- Phase 2: Tournament ---
    for iteration in range(1, ROUNDS + 1):
        print(f"\n--- Tournament Iteration {iteration}/{ROUNDS} ---", file=sys.stderr)

        unseen = [c for c in current_candidates if c not in hall_of_fame]

        if unseen:
            candidates_str = "\n".join(f"- {c}" for c in unseen)

            def score_with_judge(judge):
                judge_prompt = f"""{static_context}

{judge['persona']}

Evaluate these candidate titles for a {genre_name} novel.
Score each from 1 to 100 based on Intrigue, Genre Fit, and Memorability.

<candidates>
{candidates_str}
</candidates>

Return ONLY a valid JSON object mapping each title string to its integer score.
No explanations. Example:
{{"Title One": 85, "Title Two": 92}}"""
                try:
                    raw_scores = call_judge(judge_prompt, system_prompt=judge["persona"], temp=0.1)
                    scores = parse_json_response(raw_scores)
                    if not isinstance(scores, dict):
                        print(f"    WARNING: {judge['name']} returned non-dict, skipping", file=sys.stderr)
                        return judge["key"], {}
                    return judge["key"], scores
                except Exception as e:
                    print(f"    WARNING: {judge['name']} failed: {e}, skipping", file=sys.stderr)
                    return judge["key"], {}

            all_judge_scores = {}
            print(f"  Evaluating {len(unseen)} candidates across {len(judges)} judges in parallel...", file=sys.stderr)
            with ThreadPoolExecutor(max_workers=len(judges)) as pool:
                fut_map = {pool.submit(score_with_judge, j): j for j in judges}
                for fut in as_completed(fut_map):
                    judge_key, scores = fut.result()
                    for title in unseen:
                        score = scores.get(title)
                        if score is None:
                            # Normalize comparisons to handle minor LLM formatting differences (e.g. asterisks/quotes)
                            def normalize(s):
                                return re.sub(r'[\s\*_"\']+', '', s).lower()
                            norm_title = normalize(title)
                            for k, v in scores.items():
                                if normalize(k) == norm_title:
                                    try:
                                        score = int(v)
                                    except (ValueError, TypeError):
                                        pass
                                    break
                        if score is None:
                            score = 50
                        all_judge_scores.setdefault(title, []).append(score)

            # Aggregate into Hall of Fame
            for title, score_list in all_judge_scores.items():
                avg = sum(score_list) / len(score_list) if score_list else 50.0
                hall_of_fame[title] = {"scores": score_list, "avg": avg}

        # Print per-judge top 3
        print("\n--- Per-Judge Top 3 ---", file=sys.stderr)
        for judge in judges:
            scored = []
            for title, entry in hall_of_fame.items():
                idx = next((i for i, j in enumerate(judges) if j["key"] == judge["key"]), None)
                if idx is not None and idx < len(entry["scores"]):
                    scored.append((title, entry["scores"][idx]))
            scored.sort(key=lambda x: x[1], reverse=True)
            top3 = scored[:3]
            if top3:
                print(f"  [{judge['name']}]", file=sys.stderr)
                for t, s in top3:
                    print(f"    {s:3d}  {t}", file=sys.stderr)

        # Print aggregate top 5
        ranked = sorted(hall_of_fame.items(), key=lambda x: x[1]["avg"], reverse=True)
        print(f"\n--- Aggregate Top {min(5, len(ranked))} ---", file=sys.stderr)
        for title, entry in ranked[:5]:
            score_list = entry["scores"]
            avg = entry["avg"]
            scores_str = "/".join(str(s) for s in score_list)
            print(f"  [{avg:5.1f}] ({scores_str}) {title}", file=sys.stderr)

        if iteration == ROUNDS:
            break

        # Mutate
        top_survivors = ranked[:TOP_K_SURVIVORS]
        top_titles_str = "\n".join(f"- {t[0]}" for t in top_survivors)

        mutate_prompt = f"""{static_context}

You are a creative book naming assistant. Here are the highest-scoring titles so far:
<top_titles>
{top_titles_str}
</top_titles>

Brainstorm {MUTATIONS_PER_ROUND} NEW mutated variations of these titles.
Combine them, tweak words, change perspectives, or use similar metaphors.
Return ONLY a numbered list."""

        print("Mutating top candidates...", file=sys.stderr)
        raw_mutations = call_writer(mutate_prompt, temp=0.85)
        current_candidates = parse_titles_list(raw_mutations)
        print(f"Generated {len(current_candidates)} new mutations.", file=sys.stderr)

    # --- Phase 3: Winner ---
    ranked = sorted(hall_of_fame.items(), key=lambda x: x[1]["avg"], reverse=True)
    winner, winner_entry = ranked[0]
    winner_score = winner_entry["avg"]

    print(f"\n{'='*50}", file=sys.stderr)
    print(f"  WINNER: '{winner}' (avg score: {winner_score:.1f})", file=sys.stderr)
    scores_str = "/".join(str(s) for s in winner_entry["scores"])
    print(f"  Per-judge: {scores_str}", file=sys.stderr)
    print(f"{'='*50}", file=sys.stderr)

    # Persist
    state_path = paths.get_state_path()
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state["title"] = winner
    paths.save_json_atomic(state, state_path)

    registry_path = paths.get_registry_path()
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}

    if project_name not in registry:
        registry[project_name] = {
            "genre": genre_name,
            "created_at": state.get("created_at", ""),
            "last_modified": state.get("last_modified", ""),
            "phase": state.get("phase", "foundation"),
        }
    registry[project_name]["title"] = winner

    paths.save_registry(registry, registry_path)
    print(f"Saved to state.json and registry.json", file=sys.stderr)


if __name__ == "__main__":
    main()
