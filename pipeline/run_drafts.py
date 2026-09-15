#!/usr/bin/env python3
"""Batch draft chapters with quick slop checks and spot-check evals.

A thin loop over draft_chapter.py: drafts each chapter in a range, runs the
mechanical slop scorer on the result, and optionally runs full judge evals on
spot-check chapters (midpoint, all-is-lost, finale...).

Usage:
  uv run python run_drafts.py --from 11 --to 24
  uv run python run_drafts.py --project mynovel --from 1 --to 8 --spot 4 8
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core import paths
from core import _utf8
import argparse
import json
import re
import shlex
import subprocess
import sys

from core.paths import get_chapters_dir, get_state_path


def run(cmd, timeout=None):
    from pipeline.pipeline_infra import timeout_for
    r = subprocess.run(shlex.split(cmd), capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout or timeout_for("standard"))
    return r.stdout + r.stderr, r.returncode


def slop_check(ch):
    code = (
        "from evaluate import slop_score, load_chapter; import json; "
        f"print(json.dumps(slop_score(load_chapter({ch}))))"
    )
    out, _ = run(f'"{sys.executable}" -c "{code}"')
    return json.loads(out.strip().splitlines()[-1])


def pattern_check(ch):
    text = (get_chapters_dir() / f"ch_{ch:02d}.md").read_text(encoding="utf-8")
    words = len(text.split())
    didnot = len(re.findall(r"He did not|He had not", text))
    thought = len(re.findall(r"He thought about|He thought of", text))
    return words, didnot, thought


def spot_eval(ch):
    from pipeline.pipeline_infra import timeout_for
    out, rc = run(f'"{sys.executable}" evaluate.py --chapter={ch}', timeout=timeout_for("short"))
    m_overall = re.search(r"overall_score: ([\d.]+)", out)
    m_raw = re.search(r"raw_judge_score: (\d+)", out)
    if m_overall and m_raw:
        return float(m_overall.group(1)), int(m_raw.group(1))
    return None, None


def update_state(ch):
    state_path = get_state_path()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_focus"] = f"ch_{ch:02d}"
    state["chapters_drafted"] = ch
    from core.paths import save_json_atomic
    save_json_atomic(state, state_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=None, help="Project name (under projects/)")
    parser.add_argument("--from", dest="first", type=int, required=True)
    parser.add_argument("--to", dest="last", type=int, required=True)
    parser.add_argument("--spot", type=int, nargs="*", default=[],
                        help="Chapter numbers to spot-check with full judge eval")
    args = parser.parse_args()

    if args.project:
        paths.set_project_name(args.project)

    results = []
    for ch in range(args.first, args.last + 1):
        print(f"\n{'='*50}")
        print(f"DRAFTING CH {ch}")
        print(f"{'='*50}")

        out, rc = run(f'"{sys.executable}" pipeline/draft_chapter.py {ch}')
        if rc != 0:
            print(f"  DRAFT FAILED: {out[:200]}")
            results.append((ch, 0, 0, "FAILED"))
            continue

        words, didnot, thought = pattern_check(ch)
        slop = slop_check(ch)

        print(f"  Words: {words}")
        print(f"  Slop penalty: {slop['slop_penalty']}")
        print(f"  Tier1: {len(slop['tier1_hits'])}  Fiction: {len(slop['fiction_ai_tells'])}  Telling: {slop['telling_violations']}")
        print(f"  Patterns: didnot={didnot} thought={thought}")

        score = None
        if ch in args.spot:
            print(f"  === SPOT-CHECK EVAL ===")
            score, raw = spot_eval(ch)
            if score is not None:
                print(f"  Score: {score} (raw {raw})")
                if score < 6.0:
                    print(f"  *** BELOW THRESHOLD -- flagging for retry ***")
            else:
                print(f"  Eval parse failed")

        results.append((ch, words, slop["slop_penalty"], score))

        run(f"git add chapters/ch_{ch:02d}.md state.json")
        update_state(ch)

    print(f"\n\n{'='*60}")
    print("BATCH DRAFTING COMPLETE")
    print(f"{'='*60}")
    total_words = 0
    for ch, words, penalty, score in results:
        status = f"score={score}" if score else "drafted"
        print(f"  Ch {ch:2d}: {words:5d}w  slop={penalty:.1f}  {status}")
        total_words += words
    print(f"\n  Total new words: {total_words}")
    print(f"  Chapters drafted: {len([r for r in results if r[1] > 0])}")


if __name__ == "__main__":
    main()
