import { useEffect, useMemo, useRef, useState } from 'react'
import { useApp } from '../../state.jsx'
import { api } from '../../api/client.js'
import { useApi } from '../../api/useApi.js'
import { fmtChapter } from '../../format.js'
import { navigate, projectRoute } from '../../router.js'
import { Button, Card, EmptyState, Hint, SectionHead, StatTile, TabBar, Unavailable } from '../../components/ui.jsx'
import { EvalPane, LlmFeed } from './panels.jsx'

/**
 * Unified pipeline dashboard — one view for everything the run emits.
 * Merges the old Monitor (live run), Inspector (evaluations + llm events),
 * and Costs screens into tabbed panes: run log, score history, evaluation
 * reports, llm calls, and token telemetry.
 */

const TABS = [
  { id: 'run', label: 'run log' },
  { id: 'scores', label: 'scores' },
  { id: 'evals', label: 'evaluations' },
  { id: 'llm', label: 'llm calls' },
  { id: 'telemetry', label: 'telemetry' },
]

const PHASES = [
  { id: 'foundation', meta: () => `t: —` },
  { id: 'drafting', meta: (s) => `ch: ${s?.chaptersDone ?? 0}/${s?.chaptersTotal ?? '—'}` },
  { id: 'revision', meta: (s) => `cycles: ${s?.revisionCycle ?? 0}/3` },
  { id: 'export', meta: () => `fmt: pdf` },
]

// Log-line tags. The bridge classifies each stdout line (see
// routes/stream.py `_line_level`); "raw" is unclassified output, NOT an LLM
// call — tagging it "[llm]" mislabelled every line of run output.
const TAG_FOR = { banner: 'sys', step: 'run', warn: 'err', raw: 'out' }
const TAG_STYLE = {
  sys: 'text-paper',
  run: 'text-fog-200',
  out: 'text-fog-400',
  err: 'text-bad',
}

/* ------------------------------------------------------------------ run tab */

function PipelineStatus({ runState, scores }) {
  const activeIdx = PHASES.findIndex((p) => p.id === runState?.phase)
  const complete = runState?.phase === 'idle' // run finished — every phase cleared
  const lastKept = [...scores].reverse().find((p) => p.kept)
  return (
    <Card className="p-5">
      <SectionHead className="mb-4">pipeline_status</SectionHead>
      <ol className="relative space-y-5 border-l border-ink-600 pl-5">
        {PHASES.map((p, i) => {
          const done = complete || i < activeIdx
          const active = !complete && i === activeIdx
          return (
            <li key={p.id} className={`relative ${active || done ? '' : 'opacity-50'}`}>
              <span className={`absolute -left-[26px] top-0.5 flex h-4 w-4 items-center justify-center text-[10px] ${
                done ? 'bg-good/20 text-good' : active ? 'bg-accent/20' : 'bg-ink-700'
              }`}>
                {done ? '✓' : active && <span className="h-2 w-2 animate-pulse bg-accent" />}
              </span>
              <p className={`font-mono text-xs ${active ? 'text-accent' : done ? 'text-fog-200' : 'text-fog-400'}`}>
                [0{i + 1}] {p.id}
              </p>
              <p className="mt-0.5 font-mono text-[10px] text-fog-500">
                {active ? 'status: in_progress' : done ? 'status: complete' : 'status: pending'}
                {' · '}{p.meta(runState)}
              </p>
            </li>
          )
        })}
      </ol>
      {lastKept && (
        <p className="mt-4 font-mono text-[10px] text-fog-500">
          last kept attempt: <span className="text-good">{lastKept.score.toFixed(2)}</span>
          <span className="text-fog-500"> ({lastKept.phase})</span>
        </p>
      )}
    </Card>
  )
}

function RunTab({ project }) {
  const { runState, logLines, live, stopRun, resumeRun } = useApp()
  const [scores, setScores] = useState([])
  const [filter, setFilter] = useState('all')
  const [stopping, setStopping] = useState(false)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState('')
  const logRef = useRef(null)
  const running = runState?.running ?? false
  const finished = runState?.finished ?? false
  const hasProgress = (runState?.chaptersDone ?? 0) > 0
    || (runState?.foundationScore ?? 0) > 0
    || (runState?.phase && runState.phase !== 'foundation' && runState.phase !== 'idle')
  // Finished: nothing to resume (a no-phase launch just exits), so re-export.
  const startLabel = finished ? 're-export' : hasProgress ? 'resume run' : 'start run'
  const startOpts = finished ? { phase: 'export' } : {}

  useEffect(() => {
    api.getScoreHistory(project).then(setScores).catch(() => {})
  }, [project])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [logLines])

  const visible = useMemo(() => {
    if (filter === 'all') return logLines
    // Levels come from the bridge now, so filtering is structural rather than
    // a substring guess over the raw text.
    if (filter === 'steps') {
      return logLines.filter((l) => l.level === 'step' || l.level === 'banner')
    }
    if (filter === 'warn') return logLines.filter((l) => l.level === 'warn')
    return logLines.filter((l) => /error|traceback|fatal|exception/i.test(l.text))
  }, [logLines, filter])

  return (
    <div className="grid min-h-[520px] grid-cols-1 gap-5 lg:grid-cols-12">
      <div className="space-y-5 lg:col-span-4">
        <PipelineStatus runState={runState} scores={scores} />
        <Card className="p-5">
          <SectionHead className="mb-3">run_controls <Hint>The pipeline runs as a detached background process — stopping it is safe; resume by launching a new run without from-scratch.</Hint></SectionHead>
          <div className="space-y-2 font-mono text-xs">
            <p className="flex justify-between">
              <span className="text-fog-500">process</span>
              <span className={running ? 'text-good' : 'text-fog-500'}>
                {running ? `alive · pid ${runState?.pid ?? '?'}` : 'not running'}
              </span>
            </p>
            <p className="flex justify-between">
              <span className="text-fog-500">stream</span>
              <span className={live ? 'text-good' : 'text-fog-500'}>{live ? 'sse live' : 'polling'}</span>
            </p>
            {runState?.exitCode != null && !running && (
              <p className="flex justify-between">
                <span className="text-fog-500">last exit</span>
                <span className={runState.exitCode === 0 ? 'text-good' : 'text-bad'}>
                  code {runState.exitCode}
                </span>
              </p>
            )}
          </div>
          {!running && runState?.exitCode != null && runState.exitCode !== 0 && (
            <p className="mt-3 border border-bad/40 bg-bad/10 p-2 font-prose text-[11px] leading-relaxed text-fog-300">
              The last run exited with code {runState.exitCode} and is not live. Scores and logs
              below only reflect whatever it wrote before dying — open the project's
              <span className="font-mono"> logs/</span> folder for the full traceback.
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            {!running && (
              <Button
                variant="accent"
                disabled={starting}
                onClick={async () => {
                  setStarting(true)
                  setStartError('')
                  try {
                    await resumeRun(project, startOpts)
                  } catch (e) {
                    setStartError(e?.message || String(e))
                  } finally {
                    setStarting(false)
                  }
                }}
              >
                {starting ? 'launching…' : startLabel}
              </Button>
            )}
            <Button
              variant="danger"
              disabled={!running || stopping}
              onClick={async () => { setStopping(true); await stopRun(); setStopping(false) }}
            >
              {stopping ? 'terminating…' : 'terminate run'}
            </Button>
          </div>
          {startError && (
            <p className="mt-2 border border-bad/40 bg-bad/10 p-2 font-mono text-[11px] text-bad">
              {startError}
            </p>
          )}
          <p className="mt-3 font-prose text-[11px] leading-relaxed text-fog-500">
            logs persist under the project's logs/ folder — nothing is lost by closing this page.
            Resume continues from state.json (current phase) without wiping the project.
          </p>
        </Card>
      </div>

      <Card className="flex min-h-0 flex-col overflow-hidden lg:col-span-8">
        <div className="flex shrink-0 items-center justify-between border-b border-line px-4 py-2.5">
          <SectionHead>
            stdout_stream{' '}
            <Hint below>
              Raw output of the pipeline. Opening this backfills the tail of the run's log, so a
              finished run is readable too; new lines stream in live while a run is active.
              Filter by level, or download the whole log.
            </Hint>
          </SectionHead>
          <div className="flex items-center gap-1">
            {['all', 'steps', 'warn', 'errors'].map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`border px-2 py-0.5 font-mono text-[10px] transition-colors ${
                  filter === f ? 'border-accent/50 bg-accent/10 text-accent' : 'border-line text-fog-500 hover:text-fog-300'
                }`}
              >
                [{f}]
              </button>
            ))}
            <a
              href={api.logUrl(project)}
              download
              title="download the raw log"
              className="ml-2 border border-line px-2 py-0.5 font-mono text-[10px] text-fog-500 transition-colors hover:border-accent/50 hover:text-accent"
            >
              download
            </a>
          </div>
        </div>
        <div ref={logRef} className="min-h-96 flex-1 overflow-y-auto bg-ink-950 p-4 font-mono text-xs leading-relaxed">
          {visible.length === 0 ? (
            <p className="text-fog-500">
              [ waiting for output — lines appear here live while the pipeline writes ]
            </p>
          ) : (
            visible.map((l, i) => {
              const tag = TAG_FOR[l.level] ?? 'llm'
              return (
                <p key={i} className={TAG_STYLE[tag]}>
                  <span className="mr-3 inline-block w-16 select-none text-fog-500">{l.ts?.slice(11, 19)}</span>
                  <span className="mr-2 select-none">[{tag}]</span>
                  {l.text}
                </p>
              )
            })
          )}
        </div>
      </Card>
    </div>
  )
}

/* --------------------------------------------------------------- scores tab */

function ScoresTab({ project }) {
  const { data: scores, error, loading, retry } = useApi(
    () => api.getScoreHistory(project), [project])

  if (loading) return <div className="h-64 animate-pulse bg-ink-800" />
  if (error) return <Unavailable what="score history" error={error} onRetry={retry} />
  if (!scores.length) {
    return (
      <EmptyState icon="⌗" title="nothing scored yet">
        every draft attempt lands here once the judge scores it — foundation iterations, chapter attempts, and
        revision passes, with the keep/discard verdict.
      </EmptyState>
    )
  }

  const kept = scores.filter((s) => s.kept)
  const dropped = scores.filter((s) => !s.kept)
  const avg = (a) => (a.length ? a.reduce((x, y) => x + y.score, 0) / a.length : null)

  return (
    <div className="space-y-5">
      <div className="dock grid grid-cols-2 gap-px md:grid-cols-4">
        <StatTile label="attempts" value={String(scores.length)} sub={`${kept.length} kept · ${dropped.length} discarded`} />
        <StatTile label="avg kept" value={avg(kept)?.toFixed(2) ?? '—'} />
        <StatTile label="avg discarded" value={avg(dropped)?.toFixed(2) ?? '—'} />
        <StatTile label="best" value={Math.max(...scores.map((s) => s.score)).toFixed(2)} accent />
      </div>
      <Card className="overflow-hidden">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-line font-mono text-[10px] uppercase tracking-wide text-fog-500">
              <th className="px-4 py-2.5 font-medium">#</th>
              <th className="px-4 py-2.5 font-medium">phase</th>
              <th className="px-4 py-2.5 text-right font-medium">score</th>
              <th className="px-4 py-2.5 text-right font-medium">verdict</th>
            </tr>
          </thead>
          <tbody className="font-mono text-xs">
            {[...scores].reverse().map((s) => (
              <tr key={s.iteration} className="border-b border-ink-800 last:border-b-0">
                <td className="px-4 py-2 text-fog-500">{String(s.iteration).padStart(3, '0')}</td>
                <td className="px-4 py-2 text-fog-300">{fmtChapter(s.phase)}</td>
                <td className={`px-4 py-2 text-right ${s.score >= 6.5 ? 'text-fog-200' : 'text-fog-400'}`}>{s.score.toFixed(2)}</td>
                <td className="px-4 py-2 text-right">
                  <span className={`border px-1 text-[10px] ${s.kept ? 'border-good/40 text-good' : 'border-bad/40 text-bad'}`}>
                    [{s.kept ? 'keep' : 'discard'}]
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------ telemetry tab */

function fmtTokens(n) {
  if (n >= 1_000_000) return `${(n / 1e6).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1e3).toFixed(1)}k`
  return String(n)
}

function TelemetryTab({ project }) {
  const { data: stats, error, loading, retry } = useApi(
    () => api.getStats(project), [project])
  // Prices come from the settings page; without them there is no honest cost.
  const { data: settings } = useApi(() => api.getSettings(), [])

  if (loading) return <div className="h-64 animate-pulse bg-ink-800" />
  if (error) return <Unavailable what="telemetry" error={error} onRetry={retry} />

  const pin = settings?.prices?.inputPerMTok
  const pout = settings?.prices?.outputPerMTok
  // A dollar figure needs both a price AND a token count. Never report either
  // as zero when the provider simply did not tell us.
  const reported = stats.tokensReported ?? 0
  const priced = reported > 0 && Number.isFinite(pin) && Number.isFinite(pout)
  const rowCost = (r) => (r.tokensIn / 1e6) * pin + (r.tokensOut / 1e6) * pout
  const totalMs = stats.durationMsTotal ?? 0

  if (!stats.callCount) {
    return (
      <EmptyState icon="≈" title="no llm spend yet">
        token usage per model role appears here once the pipeline starts calling the writer,
        judge, and review models.
      </EmptyState>
    )
  }

  return (
    <div className="space-y-5">
      <div className="dock grid grid-cols-2 gap-px xl:grid-cols-4">
        <StatTile
          label="tokens in"
          value={reported > 0 ? fmtTokens(stats.tokensInTotal) : '—'}
          sub={reported > 0 ? `${stats.callCount} calls`
                            : `${stats.callCount} calls · none reported`}
        />
        <StatTile
          label="tokens out"
          value={reported > 0 ? fmtTokens(stats.tokensOutTotal) : '—'}
          sub={reported > 0 ? undefined : 'provider sent no usage'}
        />
        <StatTile label="failed calls" value={String(stats.failedCount)} sub={stats.failedCount > 0 ? undefined : 'clean'} />
        <StatTile label="time in llm" value={`${(totalMs / 60000).toFixed(1)}m`} sub={`${Math.round(totalMs / 1000)}s total`} />
      </div>
      {reported === 0 && (
        <p className="font-mono text-[10px] leading-relaxed text-fog-500">
          [ {stats.callCount} calls recorded, none reporting token usage — this gateway returns no
          usage block, so the totals and cost are unknown rather than zero. ]
        </p>
      )}
      {reported > 0 && !priced && (
        <p className="font-mono text-[10px] leading-relaxed text-fog-500">
          [ cost omitted — set input/output USD per 1M tokens on the settings page to estimate spend. token
          counts above are measured, not estimated. ]
        </p>
      )}
      <Card className="overflow-hidden">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-line text-xs uppercase tracking-wide text-fog-500">
              <th className="px-4 py-3 font-medium">role</th>
              <th className="px-4 py-3 font-medium">model</th>
              <th className="px-4 py-3 text-right font-medium">tokens in</th>
              <th className="px-4 py-3 text-right font-medium">tokens out</th>
              <th className="px-4 py-3 text-right font-medium">est. cost</th>
              <th className="px-4 py-3 text-right font-medium">calls</th>
            </tr>
          </thead>
          <tbody className="font-mono text-sm">
            {stats.byModel.map((r) => (
              <tr key={r.modelKey} className="border-b border-ink-800 last:border-b-0">
                <td className="px-4 py-3 text-fog-200">{r.modelKey}</td>
                <td className="px-4 py-3 text-fog-500">{r.model}</td>
                <td className="px-4 py-3 text-right text-fog-200">{r.tokensIn.toLocaleString()}</td>
                <td className="px-4 py-3 text-right text-fog-200">{r.tokensOut.toLocaleString()}</td>
                <td className="px-4 py-3 text-right text-accent">
                  {priced ? `$${rowCost(r).toFixed(2)}` : '—'}
                </td>
                <td className="px-4 py-3 text-right text-fog-400">{r.calls}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------ screen */

export default function PipelineDashboard({ project, tab }) {
  const activeTab = TABS.some((t) => t.id === tab) ? tab : 'run'

  useEffect(() => {
    document.title = `gesaku · ${project} · pipeline`
  }, [project])

  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="section-head">the run, end to end</p>
          <h1 className="mt-1 font-display text-2xl font-semibold lowercase tracking-tight text-paper">pipeline dashboard</h1>
        </div>
        <TabBar
          tabs={TABS}
          active={activeTab}
          onSelect={(id) => navigate(projectRoute(project, 'pipeline', id))}
        />
      </header>

      {activeTab === 'run' && <RunTab project={project} />}
      {activeTab === 'scores' && <ScoresTab project={project} />}
      {activeTab === 'evals' && <EvalPane project={project} />}
      {activeTab === 'llm' && <LlmFeed project={project} />}
      {activeTab === 'telemetry' && <TelemetryTab project={project} />}
    </div>
  )
}
