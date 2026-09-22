#!/usr/bin/env python3
"""run_pipeline.py — novel pipeline orchestrator (CLI + phase sequencing).

Phases live in pipeline/phases/*; this module only wires them together and
owns the CLI. See Docs/pipeline/spec.md for the full process spec.

Usage:
  python run_pipeline.py --project mynovel              # resume from current state
  python run_pipeline.py --project mynovel --from-scratch
  python run_pipeline.py --project mynovel --phase foundation|drafting|revision|export
  python run_pipeline.py --project mynovel --revision-cycles 4
"""

import argparse
import atexit
import os
import subprocess
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from core import paths
from core.genre import load_genre, reload_genre

from pipeline.pipeline_infra import (
    CHAPTERS_TOTAL, PHASE_ORDER, Tee, count_words_in_chapters, default_state,
    ensure_gitignore_projects, ensure_project_git, fmt_score,
    genre_chapters_total, load_state, process_notes, resolve_chapters_total,
    save_state, step, timeout_for, update_registry, banner,
)
from pipeline.phases.drafting import run_drafting
from pipeline.phases.export import run_export
from pipeline.phases.foundation import _foundation_artifact_ok, _foundation_part2_ok
from pipeline.phases.foundation import run_foundation
from pipeline.phases.revision import run_revision
from pipeline.preflight import sanity_check



# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

# Artifacts removed by --from-scratch: everything the pipeline writes into the
# project root that belongs to one novel's run. A stale micro-plant store,
# outline marker, or cached validation sidecar would leak the previous novel
# into the new one. (Directories are listed separately at the call site.)
FROM_SCRATCH_STALE_FILES = (
    "world.md", "characters.md", "outline.md", "canon.md",
    "manuscript.md", "arc_summary.md", "reviews.md",
    "results.tsv", "state.json", "active_genre.json", "seed.txt",
    "open_callbacks.json", "plant_hygiene.json",
    "premise_validation.json", "retrofit_report.json",
    ".outline_roadmap.md", ".outline_part1.md", ".outline_part2.done",
)


def run_pipeline(args):
    """Run the full pipeline or a specific phase."""

    # Set active project FIRST so all path helpers resolve correctly
    paths.set_project_name(args.project)
    project_dir = paths.get_project_dir()
    project_dir.mkdir(parents=True, exist_ok=True)

    # Tee stdout/stderr to a per-run log file in projects/<name>/logs/
    # line_buffering=True so progress is visible while the run is live —
    # block-buffered logs sat at 0B for 10+ minutes.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    log_path = paths.get_logs_dir() / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_pipeline.log"
    log_fh = open(log_path, "w", encoding="utf-8", buffering=1)
    # Close the log on EVERY exit path (sanity_check's sys.exit, an unknown
    # phase, an uncaught exception), not just the success return below.
    atexit.register(log_fh.close)
    sys.stdout = Tee(log_fh, sys.stdout)
    sys.stderr = Tee(log_fh, sys.stderr)
    step(f"Pipeline log: {log_path}")

    # Git Option B: guard root .gitignore and init project repo
    ensure_gitignore_projects()
    ensure_project_git(project_dir)

    sanity_check(args)

    root_dir = paths.get_root_dir()

    # Load or initialize state
    if args.from_scratch:
        banner("STARTING FROM SCRATCH")
        import shutil

        # Clean up existing files in the project directory to prevent cross-contamination
        if project_dir.exists():
            for name in ["chapters", "briefs", "edit_logs", "eval_logs", "typeset"]:
                p = project_dir / name
                if p.is_dir():
                    try:
                        shutil.rmtree(p)
                    except Exception as e:
                        print(f"WARN: Failed to clean directory {name}: {e}", file=sys.stderr)
            # Every per-run artifact, not just the deliverables: a stale
            # micro-plant store or outline marker would leak the previous
            # novel's objects into this one.
            for name in FROM_SCRATCH_STALE_FILES:
                p = project_dir / name
                if p.is_file():
                    try:
                        p.unlink()
                    except Exception as e:
                        print(f"WARN: Failed to remove file {name}: {e}", file=sys.stderr)
            for p in project_dir.glob("retry_feedback_ch*.txt"):
                try:
                    p.unlink()
                except Exception as e:
                    print(f"WARN: Failed to remove file {p.name}: {e}", file=sys.stderr)

        # Initialize project-specific seed
        seed_dest = paths.get_seed_path()
        if not args.notes:
            if not seed_dest.exists():
                global_seed = root_dir / "seed.txt"
                if global_seed.exists():
                    print(f"\n[WARNING][CONTAMINATION RISK] No project-specific seed.txt found. Copying global seed.txt to {seed_dest}.\n", file=sys.stderr)
                    shutil.copy2(global_seed, seed_dest)
                else:
                    print("ERROR: No seed.txt found in project directory or repository root, and no --notes provided.", file=sys.stderr)
                    sys.exit(1)

        state = default_state()
        # Write user-provided chapter count into state before banner
        if args.chapters:
            try:
                state["chapters_total"] = int(args.chapters)
            except ValueError:
                pass  # non-numeric string like "short story" — let genre framework resolve
        
        # Copy template voice.md file to project directory if it exists in root
        voice_template = root_dir / "fuel" / "voice.md"
        if voice_template.exists():
            shutil.copy2(voice_template, paths.get_voice_path())
            prose_mode = getattr(args, "prose_mode", "") or os.environ.get("GESAKU_PROSE_MODE", "")
            if prose_mode:
                from core.genre import load_prose_pack
                pack = load_prose_pack(prose_mode)
                if pack:
                    voice_path = paths.get_voice_path()
                    existing = voice_path.read_text(encoding="utf-8")
                    voice_path.write_text(
                        existing + f"\n\n---\n\n## Prose mode ({prose_mode})\n\n{pack}\n",
                        encoding="utf-8",
                    )
                
        save_state(state)
    else:
        state = load_state()

    # Single owner for chapter count: genre config once written, else the
    # launch --chapters, else the default. Never silently overwrite.
    try:
        genre_total = genre_chapters_total()
        if genre_total:
            current_total = state.get("chapters_total", 0)
            if genre_total != current_total:
                if current_total:
                    step(f"Chapter count: genre config says {genre_total}, "
                         f"state said {current_total} — genre wins")
                state["chapters_total"] = genre_total
                save_state(state)
        elif not args.from_scratch and args.chapters:
            try:
                want = int(args.chapters)
                if state.get("chapters_total", 0) != want:
                    step(f"Chapter count: --chapters {want} overrides state "
                         f"{state.get('chapters_total')} (no genre config yet)")
                    state["chapters_total"] = want
                    save_state(state)
            except ValueError:
                pass
    except Exception:
        pass

    # Ensure directories exist (helpers create them)
    paths.get_chapters_dir()
    paths.get_briefs_dir()
    paths.get_edit_logs_dir()
    paths.get_eval_logs_dir()

    # Apply revision_cycles override (with legacy support for max_cycles)
    revision_cycles = args.max_cycles if args.max_cycles is not None else args.revision_cycles

    # Determine which phases to run
    if args.phase:
        # Single phase mode
        phases = [args.phase]
    else:
        # Run from current state onward
        current = state.get("phase", "foundation")
        if current == "complete_no_pdf":
            # PDF build failed on a prior run — resume from export so the run
            # doesn't re-execute foundation/drafting/revision destructively.
            current = "export"
        if current == "complete":
            print("Pipeline already complete. Use --from-scratch to restart "
                  "or --phase to run a specific phase.")
            return
        try:
            start_idx = PHASE_ORDER.index(current)
        except ValueError:
            start_idx = 0
        phases = PHASE_ORDER[start_idx:]

    banner(f"GESAKU PIPELINE — phases: {', '.join(phases)}")
    print(f"  State: phase={state.get('phase')}, "
          f"foundation_score={state.get('foundation_score', 0)}, "
          f"chapters={state.get('chapters_drafted', 0)}/{state.get('chapters_total', '?')}, "
          f"novel_score={fmt_score(state.get('novel_score'))}")

    start_time = datetime.now()

    for phase in phases:
        try:
            if phase == "foundation":
                global CHAPTERS_TOTAL

                # Step 0: Process user notes → auto-create seed.txt
                notes_for_genre = None
                if args.notes:
                    notes_for_genre = process_notes(args.notes, args.genre)
                seed_path = paths.get_seed_path()
                if not seed_path.exists():
                    print(f"ERROR: seed.txt not found at {seed_path}", file=sys.stderr)
                    sys.exit(1)
                # TODO: --continue mode — if pre-written chapters exist, generate
                # an outline that picks up from the last written beat instead of
                # starting from chapter 1.

                # Step 1: Initialize genre configuration
                active_genre_path = paths.get_active_genre_path()
                # Only (re)build genre when missing or --from-scratch. Passing
                # --genre on a normal resume used to force a regen and clobber
                # an existing active_genre.json (chapter count / prompts).
                should_init_genre = args.from_scratch or not active_genre_path.exists()
                if should_init_genre and args.genre:
                    banner("STEP 1: Initializing genre configuration")
                    cmd = [sys.executable, str(root_dir / "foundation" / "gen_genre_framework.py")]
                    if args.genre:
                        cmd += ["--genre", args.genre]
                    if args.chapters:
                        cmd += ["--chapters", args.chapters]
                    if args.words_per_chapter:
                        cmd += ["--words-per-chapter", str(args.words_per_chapter)]
                    if notes_for_genre:
                        cmd += ["--notes", notes_for_genre]
                    if args.perspective:
                        cmd += ["--perspective", args.perspective]
                    if getattr(args, "prose_mode", ""):
                        cmd += ["--prose-mode", args.prose_mode]
                    subprocess.run(cmd, check=True, timeout=timeout_for("standard"))
                    from core.genre import reload_genre
                    reload_genre()
                    # Genre is the source of truth for chapter count once written.
                    resolve_chapters_total(state)
                    print(f"  chapters_total resolved → {state.get('chapters_total')}")
                    print("Genre config ready.\n")

                state = run_foundation(state)
            elif phase == "drafting":
                state = run_drafting(state)
            elif phase == "revision":
                state = run_revision(
                    state,
                    max_cycles=revision_cycles,
                    skip_adversarial_editing=args.skip_adversarial_editing,
                    skip_mechanical_cuts=args.skip_mechanical_cuts,
                    skip_reader_panel=args.skip_reader_panel,
                    skip_targeted_revisions=args.skip_targeted_revisions,
                    skip_full_novel_eval=args.skip_full_novel_eval,
                    skip_opus_review=args.skip_opus_review
                )
            elif phase == "export":
                state = run_export(state, skip_epub=args.no_epub)
            else:
                print(f"Unknown phase: {phase}")
                sys.exit(1)
        except KeyboardInterrupt:
            banner("INTERRUPTED — state saved")
            save_state(state)
            sys.exit(130)
        except Exception as e:
            print(f"\n  FATAL ERROR in {phase}: {e}")
            save_state(state)
            raise

    elapsed = datetime.now() - start_time
    hours = elapsed.total_seconds() / 3600

    # Update project registry with final metadata
    update_registry(args.project, {
        "title": state.get("title", args.project),
        "genre": args.genre or os.getenv("GESAKU_GENRE", "unknown"),
        "created_at": state.get("created_at", datetime.now().isoformat()),
        "last_modified": datetime.now().isoformat(),
        "phase": state.get("phase", "unknown"),
        "novel_score": state.get("novel_score"),
        "word_count": count_words_in_chapters(),
    })

    banner("PIPELINE COMPLETE")
    print(f"  Project:    {args.project}")
    print(f"  Time:       {hours:.1f} hours")
    print(f"  Phase:      {state.get('phase')}")
    print(f"  Foundation: {state.get('foundation_score', 0)}")
    print(f"  Chapters:   {state.get('chapters_drafted', 0)}/{state.get('chapters_total', '?')}")
    print(f"  Words:      {count_words_in_chapters()}")
    print(f"  Novel:      {fmt_score(state.get('novel_score'))}")
    print(f"  Cycles:     {state.get('revision_cycle', 0)}")

    # Restore stdout/stderr and close the log file
    sys.stdout = sys.stdout.original
    sys.stderr = sys.stderr.original
    log_fh.close()


def main():
    parser = argparse.ArgumentParser(
        description="Gesaku pipeline orchestrator — seed to finished novel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python run_pipeline.py --project mynovel              # resume from current state
  python run_pipeline.py --project mynovel --from-scratch  # start fresh from seed.txt
  python run_pipeline.py --project mynovel --phase foundation  # run only foundation
  python run_pipeline.py --project mynovel --phase drafting    # run only drafting
  python run_pipeline.py --project mynovel --phase revision    # run only revision
  python run_pipeline.py --project mynovel --phase export      # run only export
  python run_pipeline.py --project mynovel --max-cycles 4      # limit revision to 4 cycles
""")

    parser.add_argument(
        "--project", default=os.environ.get("GESAKU_PROJECT", "default"),
        help="Project name (creates isolated session in projects/<name>/)")
    parser.add_argument(
        "--from-scratch", action="store_true",
        help="Reset state and start from seed.txt")
    parser.add_argument(
        "--phase", choices=PHASE_ORDER,
        help="Run only a specific phase")
    parser.add_argument(
        "--max-cycles", type=int, default=None,
        help="Maximum revision cycles (deprecated synonym for --revision-cycles)")
    parser.add_argument(
        "--revision-cycles", type=int, default=6,
        help="Number of revision cycles (default: 6)")
    parser.add_argument(
        "--skip-adversarial-editing", action="store_true",
        help="Skip adversarial editing phase inside revision cycle")
    parser.add_argument(
        "--skip-mechanical-cuts", action="store_true",
        help="Skip mechanical cuts phase inside revision cycle")
    parser.add_argument(
        "--skip-reader-panel", action="store_true",
        help="Skip reader panel phase inside revision cycle")
    parser.add_argument(
        "--skip-targeted-revisions", action="store_true",
        help="Skip targeted revisions phase inside revision cycle")
    parser.add_argument(
        "--skip-full-novel-eval", action="store_true",
        help="Skip full novel evaluation phase inside revision cycle")
    parser.add_argument(
        "--skip-opus-review", action="store_true",
        help="Skip Opus review loop phase")
    parser.add_argument(
        "--no-epub", dest="no_epub", action="store_true",
        help="Skip EPUB generation at export (the PDF is unaffected)")
    parser.add_argument(
        "--perspective", default=os.environ.get("GESAKU_PERSPECTIVE", ""),
        choices=["", "first_person", "third_person"],
        help="Force narrative perspective (first_person / third_person). "
             "Empty = foundation decides.")
    parser.add_argument(
        "--prose-mode", dest="prose_mode",
        default=os.environ.get("GESAKU_PROSE_MODE", ""),
        choices=["", "first_intimate", "first_voicey", "third_close", "third_scene"],
        help="Prose distance pack (fuel/prose/*.md). Empty = no pack.")
    parser.add_argument("--genre", default=os.environ.get("GESAKU_GENRE", ""),
                        help="Genre description (e.g., 'Cyberpunk Noir')")
    parser.add_argument("--chapters", default=os.environ.get("GESAKU_CHAPTERS", "24"),
                        help="Number of chapters (or 'short story', 'novella', etc.)")
    parser.add_argument("--words-per-chapter", type=int, default=3200,
                        help="Target word count per chapter (default: 3200)")
    parser.add_argument("--notes", default=os.environ.get("GESAKU_NOTES", ""),
                        help="Story premise or file path (e.g., --notes my_ideas.txt). "
                             "Auto-expands if <300 words, auto-summarizes if >1500.")

    args = parser.parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
