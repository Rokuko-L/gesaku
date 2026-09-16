# WORKFLOW

Step-by-step guide to running gesaku.

For the full technical pipeline specification, see [pipeline/spec.md](pipeline/spec.md).

---

## Quick Start

```bash
# 1. Setup
cd ~/gesaku
cp .env.example .env   # Add your Anthropic API key

# 2. Generate a seed concept (or write your own in seed.txt)
uv run python seed.py

# 3. Create a branch for your novel
git checkout -b gesaku/my-novel

# 4. Run the full pipeline
uv run python run_pipeline.py --from-scratch
```

> [!IMPORTANT]
> **Project Isolation & Seed Requirement**:
> On the first run of a new project using `--from-scratch`, the pipeline requires either the `--notes` argument (which dynamically generates a project-specific seed) or a pre-existing project-specific seed at `projects/<project_name>/seed.txt`. If neither is provided, the pipeline will log a `[CONTAMINATION RISK]` warning and fall back to copying the root `seed.txt` template to the project space, or fail loudly if no template exists.

> [!NOTE]
> **`--notes` accepts a raw string or a file path.** Input is treated as a
> path only when it plausibly is one (under 260 chars, single line);
> anything else — including long inline premises — is used as the premise
> text directly. Short notes (<300 words) are LLM-expanded, long ones
> (>1500 words) are LLM-summarized for the genre framework while the full
> text lands in the project's `seed.txt`.


The pipeline will:
1. Build the world, characters, outline, and voice (Phase 1)
2. Draft all chapters sequentially (Phase 2)
3. Revise through automated cycles + Opus review (Phase 3)
4. Export to manuscript, PDF, ePub (Phase 4)

---

## Running Phases Individually

```bash
# Foundation only
uv run python run_pipeline.py --phase foundation

# Drafting only
uv run python run_pipeline.py --phase drafting

# Revision only (with max cycle limit)
uv run python run_pipeline.py --phase revision --max-cycles 5

# Export only
uv run python run_pipeline.py --phase export
```

---

## Manual Tools

### Evaluation
```bash
uv run python pipeline/evaluate.py --phase=foundation   # Score planning docs
uv run python pipeline/evaluate.py --chapter=5           # Score a chapter
uv run python pipeline/evaluate.py --full                # Score the whole novel
```

### Revision
```bash
uv run python adversarial_edit.py all           # Find cuts in all chapters
uv run python apply_cuts.py all --types OVER-EXPLAIN REDUNDANT
uv run python pipeline/reader_panel.py                   # 4-persona evaluation
uv run python pipeline/review.py                         # Opus dual-persona review
uv run python pipeline/gen_brief.py --auto               # Auto-generate revision brief
uv run python pipeline/gen_revision.py 5 briefs/ch05.md  # Rewrite chapter from brief
```

### Export
```bash
uv run python build_outline.py                  # Rebuild outline
uv run python build_arc_summary.py              # Rebuild summaries
python3 typeset/build_tex.py && cd typeset && tectonic novel.tex  # PDF
uv run python typeset/build_epub.py             # EPUB (no toolchain needed)
```

---

## The Three Loops

```
INNER LOOP (agent, runs overnight):
  modify → evaluate → keep/discard → repeat

OUTER LOOP (you, when you check in):
  read results → steer program.md / evaluate.py / layer files
  → let the agent run again

REVIEW LOOP (after automated revision):
  send to Opus → parse review → fix top items → repeat
  → stop when no major unqualified items remain
```

You're not writing the novel. You're programming the system that
writes the novel.
