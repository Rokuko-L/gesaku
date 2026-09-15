# State, Registry & Git Plumbing (`pipeline/pipeline_infra.py`)

Infrastructure shared by the phase modules. `run_pipeline.py` only sequences
`pipeline/phases/*` and owns the CLI; the phase implementations live in
`pipeline/phases/` (foundation, drafting, revision, review_loop, export) with
shared helpers in `pipeline/phases/common.py`. Pre-flight checks are in
`pipeline/preflight.py`.

Score parsing and chapter counting live in `pipeline/scores.py`.

## Constants

| Constant | Meaning |
|---|---|
| `FOUNDATION_THRESHOLD = 7.5` | Foundation loop exits above this |
| `FOUNDATION_PLATEAU_ITERS = 3` | Consecutive non-improving foundation iterations → proceed with best docs |
| `CHAPTER_THRESHOLD = 6.5` | Chapter retry gate |
| `MAX_FOUNDATION_ITERS / MAX_CHAPTER_ATTEMPTS / MAX_OUTLINE_ATTEMPTS` | Retry budgets |
| `MIN/MAX_REVISION_CYCLES`, `PLATEAU_DELTA` | Revision loop bounds + plateau sensitivity |
| `DECLINE_STREAK = 2` | Consecutive dropping cycles → stop revision early |
| `TIMEOUT_SHORT/STANDARD/LONG/XLONG` | Subprocess caps (see Timeouts below) |
| `REVISION_TOLERANCE`, `CUTS_TOLERANCE`, `NEAR_CLEAN_MARGIN`, `FORCE_KEEP_MARGIN` | Keep/discard tolerances (see Tolerances below) |
| `PHASE_ORDER` | `["foundation", "drafting", "revision", "export"]` |

Gate overrides are read at call time via helpers (defaults above; env wins):

| Helper | Env var |
|---|---|
| `foundation_threshold()` | `GESAKU_FOUNDATION_THRESHOLD` |
| `chapter_threshold()` | `GESAKU_CHAPTER_THRESHOLD` |
| `max_chapter_attempts()` | `GESAKU_MAX_CHAPTER_ATTEMPTS` |
| `min_revision_cycles()` / `max_revision_cycles()` | `GESAKU_MIN_REVISION_CYCLES` / `GESAKU_MAX_REVISION_CYCLES` |
| `plateau_delta()` | `GESAKU_PLATEAU_DELTA` |
| `timeout_for("short"\|"standard"\|"long"\|"xlong")` | `GESAKU_TIMEOUT_{SHORT,STANDARD,LONG,XLONG}` |
| `revision_tolerance()` / `cuts_tolerance()` | `GESAKU_REVISION_TOLERANCE` / `GESAKU_CUTS_TOLERANCE` |
| `near_clean_margin()` / `force_keep_margin()` | `GESAKU_NEAR_CLEAN_MARGIN` / `GESAKU_FORCE_KEEP_MARGIN` |
| `decline_streak()` | `GESAKU_DECLINE_STREAK` |

## Timeouts

One owner for every subprocess cap. Call sites ask for a **named budget**
rather than inventing a literal; `core.llm.llm_timeout()` uses the same four
names for per-call HTTP budgets, so a slow proxy is tuned with one pair of
env vars instead of a code change per stage.

| Budget | Subprocess default | Typical steps |
|---|---|---|
| `short` | 300 s | sanitize titles, cuts, latex, briefs |
| `standard` | 900 s | drafting, revision, single judge passes |
| `long` | 1800 s | full-novel evals, chapter evals, reader panel |
| `xlong` | 3600 s | foundation generation blocks |

**Derived caps.** A flat budget can expire *mid-retry* even when every
individual LLM call stayed inside its own budget. `gen_outline` retries the
roadmap up to `GESAKU_OUTLINE_ROADMAP_ATTEMPTS` (default 6) times and each
block up to 3 times, all at `llm_timeout("long")` — 5400 s worst case, above
the 3600 s `xlong` backstop. `foundation._outline_subprocess_cap()` therefore
computes the cap as `max(timeout_for("xlong"), (roadmap_attempts +
n_blocks x block_attempts) x llm_timeout("long"))`. The outer cap is only a
backstop: a genuinely hung call is bounded by its own per-call LLM timeout.

`run_tool` honours `check=True` on timeout: with `check=False` it returns
`rc=-1` for graceful handling, but with `check=True` (i.e. every `uv_run`)
the `TimeoutExpired` **propagates**. Previously the timeout path fabricated
a `rc=-1` result regardless, so a timed-out stage surfaced later as a bogus
`ValueError` from `parse_score("")` far from the real cause.

## Tolerances (keep/discard policy)

Four budgets, deliberately different magnitudes:

| Constant | Value | Rule |
|---|---|---|
| `REVISION_TOLERANCE` | 0.8 | A prose rewrite may regress this much and still be kept (LLM rewrites are high-variance; a marginal loss usually carries gains elsewhere). Applies to adversarial edits, targeted revisions, and Opus review revisions. |
| `CUTS_TOLERANCE` | 0.05 | Mechanical cuts must be ~score-neutral. A deterministic pass that lowers the score is a straight loss. |
| `NEAR_CLEAN_MARGIN` | 1.0 | A draft within `gate - 1.0` whose mechanical penalties are negligible is **kept, not regenerated** — deleting it and drafting blind regresses more than the miss costs. |
| `FORCE_KEEP_MARGIN` | 2.0 | Below `gate - 2.0` the chapter is marked `skipped` rather than force-kept. |

## Chapter-count ownership

**The genre config owns the number.** `state.json["chapters_total"]` is a
mirror, never the source of truth.

- `core.genre.chapters_total()` — reads `generation.outline.estimated_chapters`.
  Its config cache is keyed by the resolved config *path*, not global: one
  process can serve several projects (the webui bridge switches projects per
  request under a lock), and a single global slot would hand back the previous
  project's chapter count and prompts.
- `pipeline_infra.resolve_chapters_total(state)` — genre first, then state,
  then `CHAPTERS_TOTAL`; writes the resolved value back to state.
- Foundation generators (`gen_outline`, `gen_outline_part2`, `gen_canon`)
  all call the same resolver — they used to default to 30 / 30 / 24
  respectively, which is how a resume could silently resize the novel.
- `get_total_chapters(state)` is a deprecated shim that delegates to the
  resolver.
- `--chapters` only takes effect before a genre config exists; once
  `active_genre.json` is written, startup logs the divergence and genre wins.

## State & Registry

- `load_state / default_state / save_state` — `projects/<name>/state.json`
  checkpointing; written after every phase/step so crashes resume cleanly
  (rerun without `--from-scratch`). `save_state` delegates to
  `paths.save_json_atomic`. `novel_score` is `null` until a real full-novel
  score exists (`0.0` means "scored zero"; use `store_novel_score` /
  `fmt_score` — plateau logic skips until both sides are numeric).
- `record_novel_score(state, score, commit)` — stores the latest score **and**
  tracks the all-time peak in `best_novel_score` / `best_novel_commit`.
  `best_novel_checkpoint(state)` reads them back. `run_revision` restores the
  best-scoring commit's chapters before export when the latest cycle scored
  lower, so tolerance-passing-but-worse cycles can't cost quality at ship
  time (this happened: a novel exported at 6.86 when 7.65 existed).
- `load_registry / update_registry` — `projects/registry.json` session
  registry (atomic via `paths.save_registry` → `save_json_atomic`).
- `log_result(commit, phase, score, words, verdict, note)` — appends to
  `results.tsv`; one row per attempt (`keep`/`discard`/`forced`/`skipped`/
  `unevaluated`/`cycle`/`export`). `unevaluated` marks an attempt whose judge
  output was unusable — a real event, distinguishable from a scored 0.

## Git Keep/Discard

The pipeline treats git as an undo system:

- `git_add_commit(msg)` → commit staged work, return short hash
- `git_commit_staged(msg)` → commit only what's staged
- `git_reset_hard(ref)` → revert a rejected attempt. Its `git clean -fd`
  spares the timestamped artifact logs **and** `open_callbacks.json`: the
  plant store is chapter-scoped state whose last write may not be committed
  yet, and `git clean` only removes untracked paths — so without the
  exclusion a reset between extraction and the next commit deletes it.
- `ensure_gitignore_projects()` / `ensure_project_git(dir)` → root repo
  ignores `projects/`; each project gets its own `.git`

## Score Parsing

- `parse_score(stdout, key)` — parses `key: N` lines from evaluator output.
  **Raises ValueError when missing** — a silently absent score must never
  reach keep/discard decisions.
- `parse_score_any(stdout, *keys)` — explicit multi-key fallback
  (e.g. novel evals print `novel_score`, older ones `overall_score`).
- `get_historical_best_for_chapter(n)` — best score+commit for chapter N
  from results.tsv; used as the revert target for regressed revisions.

## Subprocess Helpers

- `run_tool(cmd, timeout, cwd)` — capture-output runner returning
  `CompletedProcess`.
- `uv_run(script, timeout)` — `run_tool("uv run python <script>")`.
- `Tee` — duplicates stdout/stderr into per-run log files under
  `projects/<name>/logs/`.

Related: [spec.md](spec.md) · [scoring-engine.md](scoring-engine.md) ·
[../core/path-resolution.md](../core/path-resolution.md)
