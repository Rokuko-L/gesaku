# Operator Console Bridge (webui/server.py)

The webui is a React 19 + Vite single-page console (`webui/frontend/`) rendered
from real pipeline artifacts. This doc covers the FastAPI bridge that serves it
and the run lifecycle it manages.

## Run

```bash
uv run uvicorn server:app --app-dir webui --port 8600   # from repo root
cd webui/frontend && npm run dev                        # vite on :5175
```

The vite dev server proxies `/api -> http://127.0.0.1:8600`
(`webui/frontend/vite.config.js`). Interactive API docs at
`http://127.0.0.1:8600/api/docs`.

## Data flow

```
projects/<name>/ artifacts (state.json, results.tsv, eval_logs/, briefs/,
edit_logs/, chapters/, outline.md, world/characters/canon.md, logs/)
        │
        ▼
webui/server.py ── reuses scratch/gen_webui_fixtures.py generators ──> /api/*
        │                                     ▲
        ▼                                     │ launch/stop/tail
webui/frontend/src/api/client.js ── fetch + SSE (/api/stream) ──> screens
```

`contract.js` is the source of truth for response shapes; the server must
satisfy it. Project names pass through `paths.set_project_name` (path-isolation
check). The default project is the most recently touched `state.json`; the
frontend pins the active project via the hash route (`#/p/<name>/…`), mirrored
in `localStorage.gesaku_active_project`.

## Run lifecycle (webui/run_manager.py)

- `POST /api/projects` validates the creation-wizard payload (genre is
  required for fresh projects; path-isolation re-checked) and spawns
  `run_pipeline.py` through `RunManager.launch` — detached process group,
  stdout captured to `logs/<ts>_webui.log`, metadata persisted to the
  project's `run.json` so liveness survives bridge restarts.
- **Story creator (default):** `ProjectsGallery` structured form (logline,
  protagonist gist, cast + awareness, arcs, optional hidden-truth/reveal,
  optional freeform attach) is serialized client-side into `notes` before
  launch. Toggle `[paste notes]` restores the classic dump box / file path.
  `name` is the **project folder id**, not the novel title (pipeline invents
  the title; optional `workingTitle` is author-only).
- Liveness probes are signal-0-safe (`OpenProcess` on Windows — plain
  `os.kill(pid, 0)` would TerminateProcess there).
- `POST /api/run/stop` terminates the run (in-process handle, or by pid for
  runs launched by a previous bridge).
- The SSE feed tails the run's captured log, or the newest `logs/*_pipeline.log`
  when the run was started from the CLI.

## Endpoints

| Route | Serves |
|---|---|
| `GET /api/projects` | all projects under `projects/` with state (sorted by recency, `running` flagged) |
| `POST /api/projects` | creation wizard: validate + launch `run_pipeline.py` |
| `GET /api/run-state?project=` | phase/score/chapter snapshot for the project header |
| `GET /api/run-status?project=` | RunManager liveness only (`running`, `pid`, `exitCode`) |
| `POST /api/run/stop?project=` | terminate the project's live run |
| `GET /api/stream?project=` | SSE: `state` snapshots (~2s), `log` tail lines, `llm` tail events |
| `GET /api/score-history?project=` | keep/discard points from `results.tsv` |
| `GET /api/llm-events?project=` | `llm_events.jsonl` in contract camelCase |
| `GET /api/foundation?project=` | entity graph nodes/edges + world/characters/canon/voice docs |
| `GET /api/ledger?project=` | premise beats, roadmap, foreshadowing threads, `chaptersTotal` |
| `GET /api/entity-graph?project=` | LLM-arranged graph if cached, else heuristic co-mention graph |
| `POST /api/entity-graph?project=` | ask the writer model to arrange the graph (registry + world bible + canon excerpt); cached to `.entity_graph.json` with an inputs fingerprint — `stale: true` when the source docs change |
| `GET /api/chapters?project=` | chapters + per-attempt history + full prose |
| `GET /api/evals?project=` | eval-log map keyed by eval-log chapter key (`ch01`) |
| `GET /api/revision?project=` | revision briefs, adversarial cuts, novel reviews |
| `GET /api/tournament?project=` | synthesized A/B matches from discard/keep pairs |
| `GET /api/settings` | live `.env` + env-aware gate constants (models, thresholds, defaults) |
| `POST /api/settings` | merge payload into `.env` (baseUrl, optional full apiKey, models, thresholds, heuristics, defaults) and return refreshed settings |

## Frontend architecture

- Hash router (`src/router.js`): `#/projects` (landing), `#/p/<name>/<view>`
  (workspace: overview, pipeline, foundation, manuscript, revision, ledger,
  arena), `#/settings`. Three top-level nav items; the project header is
  persistent across all workspace views.
- Store (`src/state.jsx`): one SSE subscription per active project feeding the
  run state, log buffer, and llm buffer; degrades to 5s polling of run-state
  if the stream fails.
- Screens under `src/screens/project/` — the pipeline dashboard merges the
  former Monitor/Inspector/Costs screens into tabbed panes (run log, scores,
  evaluations, llm calls, telemetry). Ledger and Arena are inspection tools,
  surfaced contextually (overview cards, manuscript "compare in arena").
- `qa-shots.mjs` screenshots every route against the dev server (`node
  qa-shots.mjs [base] [outDir]`, uses system Chrome).

## Deferred (not in the bridge yet)

- Pause/resume (vs. terminate) and interactive stdin injection into the run.
  Stopping is still a hard kill; resume = launch again without `--from-scratch`.

## Tests

`scratch/test_webui_server.py` — import smoke + pure helpers (`norm_phase`,
`_mask`). Full suite stays offline; endpoint behavior is exercised manually
against real projects.
