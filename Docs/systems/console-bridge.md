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
webui/server.py ── reuses webui/fixtures.py generators ──> /api/*
        │                                     ▲
        ▼                                     │ launch/stop/tail
webui/frontend/src/api/client.js ── fetch + SSE (/api/stream) ──> screens
```

`contract.js` is the source of truth for response shapes; the server must
satisfy it. Project names pass through `paths.set_project_name` (path-isolation
check) — `deps.project_dir()` holds the lock across the *set and the read*, so a
concurrent request for another project cannot swap the global in between. The
default project is the most recently touched `state.json`; the frontend pins the
active project via the hash route (`#/p/<name>/…`), mirrored in
`localStorage.gesaku_active_project`.

`deps.llm_event_view()` is the single owner of the `llm_events.jsonl` →
API-shape mapping (snake_case → camelCase). Both `GET /api/llm-events` and the
SSE `llm` frames go through it; when only one did, live rows rendered blank
while the same call looked correct after a reload.

CORS is restricted to the loopback origins the console is served from
(`:5175` vite, `:8600` bridge). It was `allow_origins=["*"]`, which let any page
the operator visited POST `/api/settings` — repointing `ANTHROPIC_BASE_URL` at
an attacker endpoint — or spawn and kill runs.

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
- Liveness is `_pid_alive` (on Windows `GetExitCodeProcess` reporting
  `STILL_ACTIVE` — plain `os.kill(pid, 0)` would TerminateProcess there, and
  `OpenProcess(SYNCHRONIZE)` still succeeds for an already-terminated process).
- Identity is a **start token**: `launch()` records `_process_start_token(pid)`
  (Windows `GetProcessTimes`, Linux `/proc/<pid>/stat` field 22) in `run.json`,
  and `_pid_is_run(pid, token)` rejects the pid when the token differs. This is
  what stops a recycled pid from reporting the project as permanently running —
  blocking Start and aiming `stop` at an unrelated process — and it is exact
  even where `wmic` is gone (Windows 11 24H2+), unlike a command-line probe.
  `run.json` written before tokens existed falls back to that probe.
  `status()` drops a stale pid file.
- `POST /api/run/stop` kills the run's **process tree** (`taskkill /T` on
  Windows; on posix `killpg` only when the pid is its own group leader, since
  otherwise the group belongs to someone else — possibly the bridge). Runs are
  launched in their own group/session, so a plain `terminate()` left grandchild
  stages (`gen_genre_framework` etc.) alive and still spending tokens.
- The SSE feed re-resolves its log target every tick: a resumed run writes a
  *new* timestamped log while the old one still exists, so holding the first
  path tailed a dead file forever. The identity check it performs is a
  fingerprint comparison, not a subprocess — safe on the event loop.

## Endpoints

| Route | Serves |
|---|---|
| `GET /api/projects` | all projects under `projects/` with state (sorted by recency, `running` flagged). An empty shelf returns `[]`, not 404 — a 404 made the UI substitute its offline sample projects |
| `POST /api/projects` | creation wizard: validate + launch `run_pipeline.py` |
| `DELETE /api/projects/{name}` | delete a project's workspace + saved seed. Refuses while a run is live; refuses any path outside `projects/` |
| `GET /api/artifacts?project=` | deliverables present on disk (pdf, epub, manuscript, outline, arcSummary) with size, mtime and download URL |
| `GET /api/artifacts/{kind}?project=` | download/open one deliverable (the typeset PDF or EPUB is the usual one) |
| `GET /api/run-state?project=` | phase/score/chapter snapshot for the project header |
| `GET /api/run-status?project=` | RunManager liveness only (`running`, `pid`, `exitCode`) |
| `POST /api/run/stop?project=` | kill the project's live run and its process tree |
| `GET /api/stream?project=` | SSE: `state` snapshots (~2s), `log` tail lines, `llm` tail events (mapped with `llm_event_view`) |
| `GET /api/score-history?project=` | keep/discard points from `results.tsv` |
| `GET /api/llm-events?project=` | `llm_events.jsonl` in contract camelCase (incl. `error`) |
| `GET /api/foundation?project=` | entity graph nodes/edges + world/characters/canon/voice docs |
| `GET /api/ledger?project=` | premise beats, roadmap, planned threads, foreshadowing threads, callbacks, `chaptersTotal`, `settled`. Each thread row carries `status` (`matched` / `plant-only` / `harvest-only`), `matchMethod` (`declared` / `slug` / `inferred`, or `null` when nothing is paired), and `span` (arc length, `null` unless both ends are known). A payoff with no setup is an **orphan**, drawn as a lone marker — not a closed loop |
| `GET /api/entity-graph?project=` | LLM-arranged graph if cached, else heuristic co-mention graph |
| `POST /api/entity-graph?project=` | ask the writer model to arrange the graph (registry + world bible + canon excerpt); cached to `.entity_graph.json` with an inputs fingerprint — `stale: true` when the source docs change |
| `GET /api/chapters?project=` | chapters + per-attempt history + full prose |
| `GET /api/evals?project=` | eval-log map keyed by eval-log chapter key (`ch01`) |
| `GET /api/revision?project=` | revision briefs, adversarial cuts, novel reviews |
| `GET /api/tournament?project=` | synthesized A/B matches from discard/keep pairs |
| `GET /api/settings` | live `.env` + env-aware gate constants (models, thresholds, defaults, `prices`) |
| `POST /api/settings` | merge payload into `.env` (baseUrl, optional full apiKey, models, thresholds, heuristics, defaults, prices) and return refreshed settings |

## Frontend architecture

- Hash router (`src/router.js`): `#/projects` (landing), `#/p/<name>/<view>`
  (workspace: overview, pipeline, foundation, manuscript, revision, ledger,
  arena), `#/settings`. Three top-level nav items; the project header is
  persistent across all workspace views. An unknown project name renders a
  "no such project" screen instead of falling through to the sub-views.
- Store (`src/state.jsx`): one SSE subscription per active project feeding the
  run state, log buffer, and llm buffer; if the stream fails it is **closed**
  and the store degrades to 5s polling of run-state (leaving the EventSource
  open meant the failed stream and the poll both wrote run state).
- `src/api/useApi.js` gives each screen explicit `loading` / `error` / `data`
  states. `client.js` falls back to the offline fixtures when the bridge is
  **unreachable**; on an HTTP error from a live bridge it throws, so the screen
  shows an error panel (`Unavailable`) rather than another novel's prose. Two
  demo-shaped endpoints (`/api/projects`, `/api/settings`) pass
  `fixtureOnError: true` and also fall back on HTTP error — a fabricated shelf
  is acceptable there, a fabricated manuscript is not.
- A view that throws on unexpected data is caught by `components/ErrorBoundary.jsx`
  (keyed per view+project), which reports the error in place and leaves the rest
  of the console usable. Without it a single bad payload — a ledger of `null` —
  unmounted the whole app to a blank page.
- Screens under `src/screens/project/` — the pipeline dashboard merges the
  former Monitor/Inspector/Costs screens into tabbed panes (run log, scores,
  evaluations, llm calls, telemetry). Ledger and Arena are inspection tools,
  surfaced contextually (overview cards, manuscript "compare in arena").
  Overview lists the project's deliverables for download; the shelf card offers
  a hover `delete`.
- Telemetry shows cost only when `prices` is configured in settings
  (`GESAKU_PRICE_INPUT_PER_MTOK` / `..._OUTPUT_PER_MTOK`); otherwise the column
  reads `—`. It previously applied a hard-coded price map, printing confident
  dollar figures unrelated to the account being billed.
- `qa-shots.mjs` screenshots every route against the dev server (`node
  qa-shots.mjs [base] [outDir]`, uses system Chrome).
- **Serving the built SPA** (`uv run gesaku --static`, or `--dev` off): the shell
  is served with `Cache-Control: no-cache` so a rebuilt console is picked up on
  the next reload. Without it the browser applies heuristic freshness to
  `index.html`, which pins the old content-hashed bundle and silently runs stale
  frontend code until a hard reload. The hashed assets themselves stay normally
  cacheable.

## Deferred (not in the bridge yet)

- Pause/resume (vs. terminate) and interactive stdin injection into the run.
  Stopping is still a hard kill; resume = launch again without `--from-scratch`.

## Module layout

| Module | Role |
|---|---|
| `webui/server.py` | FastAPI app, project CRUD (incl. `DELETE`), run control, artifact + data routes |
| `webui/deps.py` | Project resolution (locked) + state helpers + `llm_event_view` (the one record→API mapping) |
| `webui/fixtures.py` | Artifact→API generators over `projects/<name>/` (also the offline fixture source) |
| `webui/run_manager.py` | Subprocess supervision (liveness, tree-kill, log path) |
| `webui/routes/graph.py` | `/api/entity-graph` (cached LLM arrangement + heuristic fallback) |
| `webui/routes/settings.py` | `/api/settings` — read/patch the repo `.env` |
| `webui/routes/stream.py` | `/api/stream` — SSE state snapshots + log/llm tails |

Route modules are plain `APIRouter`s, included by `server.py`. `RunManager` is
instantiated once, in `deps.py`; routes import that instance.

## Tests

`tests/test_webui_server.py` — import smoke, pure helpers (`norm_phase`,
`_mask`), and `llm_event_view` (including a source guard that the SSE feed and
the history endpoint both route through it). `tests/test_run_manager.py` covers
liveness and the stale-pid-file path. Endpoint behavior is still exercised
manually against real projects; the full suite stays offline.
