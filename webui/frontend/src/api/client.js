import projects from '../fixtures/projects.json'
import llmEvents from '../fixtures/llm-events.json'
import settings from '../fixtures/settings.json'
import chapters from '../fixtures/chapters.json'
import evals from '../fixtures/evals.json'
import revision from '../fixtures/revision.json'
import tournament from '../fixtures/tournament.json'

/**
 * API client — implements the contract in contract.js.
 * Talks to the FastAPI bridge (webui/server.py, port 8600 via the vite
 * proxy); if the server isn't up, falls back to the generated fixtures so
 * the console stays browsable offline.
 * Live updates arrive over SSE (GET /api/stream) via subscribeStream();
 * on stream failure callers can fall back to polling getRunState.
 */

/**
 * Live resource fetch.
 *  - Bridge unreachable (network error) → the offline fixture, so the console
 *    stays browsable as a demo.
 *  - Bridge answers with an HTTP error → throw, unless the caller opts in via
 *    `fixtureOnError`. Rendering another novel's prose/scores/telemetry for a
 *    real project is worse than rendering nothing.
 */
async function live(path, fallback, { fixtureOnError = false } = {}) {
  let res
  try {
    res = await fetch(path)
  } catch {
    return fallback
  }
  if (!res.ok) {
    if (fixtureOnError) return fallback
    throw new Error(`${res.status} ${path}`)
  }
  return await res.json()
}

async function send(path, body, method = 'POST') {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body == null ? {} : { body: JSON.stringify(body) }),
  })
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      detail = (await res.json()).detail ?? detail
    } catch { /* keep status code */ }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return res.json()
}

// Active project, mirrored into localStorage so a reload keeps context.
let activeProject = localStorage.getItem('gesaku_active_project') ?? ''

const q = (project) => {
  const name = project ?? activeProject
  return name ? `?project=${encodeURIComponent(name)}` : ''
}

export const api = {
  setActiveProject(name) {
    activeProject = name
    if (name) localStorage.setItem('gesaku_active_project', name)
    else localStorage.removeItem('gesaku_active_project')
  },

  getActiveProject() {
    return activeProject
  },

  async listProjects() {
    // Demo-shaped: the shelf falls back to sample projects when offline.
    return live('/api/projects', projects, { fixtureOnError: true })
  },

  async getRunState(project) {
    // Offline fallback must not invent a live run — only a dead/idle shell.
    const offline = {
      project: project || '',
      phase: 'idle',
      iteration: 0,
      foundationScore: 0,
      loreScore: 0,
      chaptersTotal: 0,
      chaptersDone: 0,
      revisionCycle: 0,
      running: false,
      pid: null,
      startedAt: null,
      runStartedAt: null,
      exitCode: null,
    }
    return live(`/api/run-state${q(project)}`, offline)
  },

  async getScoreHistory(project) {
    // Project-scoped: never fall back to the offline demo fixture — a fake
    // 7.60 is worse than an empty chart. Empty means "nothing judged yet".
    return live(`/api/score-history${q(project)}`, [])
  },

  async listLlmEvents(project) {
    return live(`/api/llm-events${q(project)}`, llmEvents)
  },

  async getStats(project) {
    const evts = await this.listLlmEvents(project)
    const ok = evts.filter((e) => e.ok)
    const sum = (k) => ok.reduce((a, e) => a + (e[k] ?? 0), 0)
    return {
      tokensInTotal: sum('tokensIn'),
      tokensOutTotal: sum('tokensOut'),
      // How many calls actually reported usage. Without this the totals read
      // as a measured zero when the truth is "the provider told us nothing".
      tokensReported: ok.filter((e) => e.tokensIn != null || e.tokensOut != null).length,
      callCount: evts.length,
      failedCount: evts.length - ok.length,
      durationMsTotal: sum('durationMs'),
      byModel: Object.entries(
        ok.reduce((acc, e) => {
          acc[e.modelKey] ??= { model: e.model, tokensIn: 0, tokensOut: 0, calls: 0 }
          acc[e.modelKey].tokensIn += e.tokensIn ?? 0
          acc[e.modelKey].tokensOut += e.tokensOut ?? 0
          acc[e.modelKey].calls += 1
          return acc
        }, {}),
      ).map(([modelKey, v]) => ({ modelKey, ...v })),
    }
  },

  async getSettings() {
    // Demo-shaped: settings renders fine from the sample when offline.
    return live('/api/settings', settings, { fixtureOnError: true })
  },

  /** Persist settings to .env; returns the refreshed settings payload. */
  saveSettings(payload) {
    return send('/api/settings', payload)
  },

  async listChapters(project) {
    return live(`/api/chapters${q(project)}`, chapters)
  },

  /** evals map is keyed by the pipeline's eval-log chapter key (`ch01`). */
  async getEvals(project, chapterId) {
    const map = await this.listEvals(project)
    return map[chapterId.replace('ch_', 'ch')] ?? []
  },

  async listEvals(project) {
    return live(`/api/evals${q(project)}`, evals)
  },

  async getRevision(project) {
    return live(`/api/revision${q(project)}`, revision)
  },

  async listMatches(project) {
    return live(`/api/tournament${q(project)}`, tournament)
  },

  async getFoundation(project) {
    return live(`/api/foundation${q(project)}`, null)
  },

  /** LLM-arranged entity graph if cached, else the heuristic co-mention graph. */
  async getEntityGraph(project) {
    return live(`/api/entity-graph${q(project)}`, null)
  },

  /** Ask the writer model to re-arrange the graph (slow — one LLM call). */
  arrangeEntityGraph(project) {
    return send(`/api/entity-graph${q(project)}`)
  },

  async getLedger(project) {
    return live(`/api/ledger${q(project)}`, null)
  },

  /** Launch run_pipeline.py for a new project (creation wizard). */
  createProject(payload) {
    return send('/api/projects', payload)
  },

  /** Delete a project's workspace. The bridge refuses while a run is live. */
  deleteProject(name) {
    return send(`/api/projects/${encodeURIComponent(name)}`, null, 'DELETE')
  },

  /** Deliverable files (pdf, epub, manuscript, outline, arcSummary) on disk. */
  async listArtifacts(project) {
    return live(`/api/artifacts${q(project)}`, [])
  },

  /** Direct download URL for one deliverable. */
  artifactUrl(project, kind) {
    return `/api/artifacts/${kind}${q(project)}`
  },

  /** Terminate the project's live run. */
  stopRun(project) {
    return send(`/api/run/stop${q(project)}`)
  },

  /**
   * Resume / continue an existing project's pipeline (no from-scratch).
   * state.json picks up the current phase.
   */
  startRun(project, payload = {}) {
    return send('/api/run/start', { project: project ?? null, ...payload })
  },

  /**
   * SSE subscription for one project. Handlers: onState(runState),
   * onLog({ts, level, text}), onLlm(llmEvent). Returns a cleanup fn.
   * Falls back to nothing on error — callers should poll getRunState then.
   */
  subscribeStream(project, { onState, onLog, onLlm }) {
    if (!window.EventSource) return () => {}
    const url = `/api/stream${q(project)}`
    const es = new EventSource(url)
    let failed = false
    es.addEventListener('state', (e) => {
      try { onState?.(JSON.parse(e.data)) } catch { /* malformed frame */ }
    })
    es.addEventListener('log', (e) => {
      try { onLog?.(JSON.parse(e.data)) } catch { /* malformed frame */ }
    })
    es.addEventListener('llm', (e) => {
      try { onLlm?.(JSON.parse(e.data)) } catch { /* malformed frame */ }
    })
    es.onerror = () => {
      // EventSource retries on its own; surface the first failure so the
      // caller can degrade to polling.
      if (!failed) {
        failed = true
        es.onerror = null
        onState?.(null, { streamFailed: true })
      }
    }
    return () => es.close()
  },
}
