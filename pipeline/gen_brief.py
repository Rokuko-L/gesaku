#!/usr/bin/env python3
"""
Auto-generate revision briefs from reader panel feedback, evaluation results,
or adversarial cuts.

Usage:
  python gen_brief.py --panel 12    # brief from panel feedback for ch 12
  python gen_brief.py --eval 12     # brief from eval callouts for ch 12
  python gen_brief.py --cuts 12     # brief from adversarial cuts for ch 12
  python gen_brief.py --auto        # auto-detect weakest chapter and generate
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
from pathlib import Path

from core import paths
from pipeline.briefs.auto import build_auto_brief
from pipeline.briefs.context import analyze_writing_sample, word_count
from pipeline.briefs.cuts import build_cuts_brief
from pipeline.briefs.eval import build_eval_brief
from pipeline.briefs.panel import build_panel_brief



def main():
    parser = argparse.ArgumentParser(
        description="Auto-generate revision briefs from feedback sources."
    )
    parser.add_argument("--panel", type=int, metavar="CH",
                        help="Generate brief from reader panel feedback for chapter CH")
    parser.add_argument("--eval", type=int, metavar="CH",
                        help="Generate brief from eval callouts for chapter CH")
    parser.add_argument("--cuts", type=int, metavar="CH",
                        help="Generate brief from adversarial cuts for chapter CH")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-detect weakest chapter and generate combined brief")
    parser.add_argument("--sample", metavar="FILE",
                        help="Writing sample file for voice calibration (analyzes rhythm, verb choice, dialogue style)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print brief to stdout without saving")

    args = parser.parse_args()

    try:
        return _main_inner(args, parser)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _main_inner(args, parser):
    calibrated_rules = []
    if args.sample:
        sample_path = Path(args.sample)
        if not sample_path.exists():
            raise FileNotFoundError(f"sample file not found: {args.sample}")
        sample_text = sample_path.read_text(encoding="utf-8")
        calibrated_rules = analyze_writing_sample(sample_text)
        if calibrated_rules:
            print(f"Voice calibration: {len(calibrated_rules)} rules derived from sample ({len(sample_text.split())} words)", file=sys.stderr)
        else:
            print("Voice calibration: sample too short (<50 words)", file=sys.stderr)

    # Validate: exactly one mode
    modes = sum([
        args.panel is not None,
        args.eval is not None,
        args.cuts is not None,
        args.auto,
    ])
    if modes == 0:
        parser.print_help()
        sys.exit(1)
    if modes > 1:
        sys.exit("ERROR: specify exactly one of --panel, --eval, --cuts, --auto")

    # Generate
    if args.panel is not None:
        ch = args.panel
        brief_text = build_panel_brief(ch, extra_rules=calibrated_rules)
        suffix = "panel"
    elif args.eval is not None:
        ch = args.eval
        brief_text = build_eval_brief(ch, extra_rules=calibrated_rules)
        suffix = "eval"
    elif args.cuts is not None:
        ch = args.cuts
        brief_text = build_cuts_brief(ch, extra_rules=calibrated_rules)
        suffix = "cuts"
    else:  # --auto
        ch, brief_text = build_auto_brief(extra_rules=calibrated_rules)
        suffix = "auto"

    if args.dry_run:
        print(brief_text)
        return

    # Save
    briefs_dir = paths.get_briefs_dir()  # also creates the directory
    out_path = briefs_dir / f"ch{ch:02d}_{suffix}.md"
    out_path.write_text(brief_text, encoding="utf-8")
    print(f"Saved: {out_path}", file=sys.stderr)
    print(f"Chapter: {ch}", file=sys.stderr)
    print(f"Type: {suffix}", file=sys.stderr)
    print(f"Brief length: {word_count(brief_text)} words", file=sys.stderr)


if __name__ == "__main__":
    main()
