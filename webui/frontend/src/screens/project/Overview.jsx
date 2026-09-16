import { useEffect, useState } from 'react'
import { useApp } from '../../state.jsx'
import { api } from '../../api/client.js'
import { useApi } from '../../api/useApi.js'
import { navigate, projectRoute } from '../../router.js'
import { Button, Card, EmptyState, Hint, SectionHead, Skel, StatTile, timeAgo } from '../../components/ui.jsx'

/**
 * Project overview — the control desk. Answers "where is this novel right
 * now, what's next, and where do I want to go?" One screen: phase tracker,
 * headline stats, and cards into every sub-view (advanced tools included,
 * surfaced contextually rather than as permanent nav).
 */

const PHASES = [
  { id: 'foundation', hint: 'The pipeline invents the world, cast, and a beat-by-beat outline, scoring each attempt against the genre framework until it clears the gate.' },
  { id: 'drafting', hint: 'Each chapter is drafted, judged for slop and quality, and retried (up to 5 attempts) until it clears the chapter gate.' },
  { id: 'revision', hint: 'The whole novel is re-read by a critic panel; adversarial editors cut and rewrite weak prose across several cycles.' },
  { id: 'export', hint: 'The manuscript is assembled, arcs summarized, and a typeset PDF is produced.' },
]

function PhaseTracker({ runState }) {
  const activeIdx = PHASES.findIndex((p) => p.id === runState?.phase)
  // `finished` comes from state.current_focus == "done"; `phase` alone cannot
  // tell a completed novel from one that never ran (both are "idle").
  const allDone = runState?.finished || runState?.phase === 'idle'
  const done = allDone || activeIdx === PHASES.length - 1 && runState?.phase === 'export'
  return (
    <Card className="p-5">
      <SectionHead className="mb-4 flex items-center gap-2">
        pipeline <Hint>The four phases run in order. Each one loops internally — generating, judging, retrying — until its quality gate is met.</Hint>
      </SectionHead>
      <ol className="relative space-y-5 border-l border-ink-600 pl-5">
        {PHASES.map((p, i) => {
          const isDone = allDone || i < activeIdx
          const active = i === activeIdx
          return (
            <li key={p.id} className={`relative ${active || isDone ? '' : 'opacity-50'}`}>
              <span className={`absolute -left-[26px] top-0.5 flex h-4 w-4 items-center justify-center text-[9px] ${
                isDone ? 'bg-good/20 text-good' : active ? 'bg-accent/20' : 'bg-ink-700'
              }`}>
                {isDone ? '✓' : active && <span className="h-2 w-2 animate-pulse bg-accent" />}
              </span>
              <p className={`font-mono text-xs ${active ? 'text-accent' : isDone ? 'text-fog-200' : 'text-fog-400'}`}>
                [0{i + 1}] {p.id}
              </p>
              <p className="mt-0.5 max-w-md font-prose text-[11px] leading-relaxed text-fog-500">{p.hint}</p>
              {active && p.id === 'drafting' && runState?.chaptersTotal > 0 && (
                <div className="mt-1.5 h-1 max-w-48 bg-ink-700">
                  <div className="h-full bg-accent" style={{ width: `${(runState.chaptersDone / runState.chaptersTotal) * 100}%` }} />
                </div>
              )}
            </li>
          )
        })}
      </ol>
      {done && (
        <p className="mt-4 font-mono text-[10px] text-good">✓ novel complete — deliverables are linked below</p>
      )}
    </Card>
  )
}

/** Deliverable downloads (the typeset PDF, manuscript, outline, arc summary). */
function Deliverables({ project }) {
  const { data: files, error, loading } = useApi(
    () => api.listArtifacts(project), [project])
  if (loading || error || !files?.length) return null
  return (
    <Card className="p-5">
      <SectionHead className="mb-3">deliverables</SectionHead>
      <div className="divide-y divide-line border border-line">
        {files.map((f) => (
          <a
            key={f.kind}
            href={api.artifactUrl(project, f.kind)}
            target="_blank"
            rel="noreferrer"
            className="group flex items-center justify-between p-3 transition-colors hover:bg-ink-850"
          >
            <span className="font-mono text-xs text-fog-200 group-hover:text-accent">{f.name}</span>
            <span className="font-mono text-[10px] text-fog-500">
              {(f.bytes / 1024).toFixed(0)} KB · {timeAgo(f.updatedAt)} ↓
            </span>
          </a>
        ))}
      </div>
    </Card>
  )
}

const CARDS = [
  { id: 'pipeline', label: 'pipeline dashboard', desc: 'live logs, score history, evaluation reports, and token telemetry — everything the run emits, tabbed.', icon: '≫' },
  { id: 'foundation', label: 'foundation', desc: 'the world bible, character registry, canon rules, and the entity graph of who-entangles-with-whom.', icon: '◈' },
  { id: 'manuscript', label: 'manuscript', desc: 'read the prose chapter by chapter with each chapter’s score and revision notes alongside.', icon: '❞' },
  { id: 'revision', label: 'revision', desc: 'reader-panel briefs and adversarial cut lists — the edits that made the novel better.', icon: '✂' },
]

const TOOLS = [
  { id: 'ledger', label: 'beats & harvests', desc: 'every planted setup and where it pays off, across the whole outline.', icon: '⌘' },
  { id: 'arena', label: 'chapter arena', desc: 'side-by-side A/B of discarded vs kept drafts — judge the fights yourself.', icon: '⚔' },
]

function MiniScores({ scores }) {
  if (!scores.length) return null
  const recent = scores.slice(-24)
  const max = 10
  return (
    <div className="flex h-16 items-end gap-1">
      {recent.map((s) => (
        <div
          key={s.iteration}
          title={`attempt ${s.iteration}: ${s.score.toFixed(2)} (${s.kept ? 'kept' : 'discarded'})`}
          className={`w-2 ${s.kept ? 'bg-good/70' : 'bg-bad/50'}`}
          style={{ height: `${Math.max(6, (s.score / max) * 100)}%` }}
        />
      ))}
    </div>
  )
}

export default function Overview({ project }) {
  const { runState, stopRun, resumeRun } = useApp()
  const [scores, setScores] = useState([])
  const [meta, setMeta] = useState(null)
  const [stopping, setStopping] = useState(false)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState('')

  useEffect(() => {
    document.title = `gesaku · ${project}`
    api.getScoreHistory(project).then(setScores).catch(() => {})
    api.listProjects().then((ps) => setMeta(ps.find((p) => p.name === project))).catch(() => {})
  }, [project])

  const running = runState?.running ?? false
  const finished = runState?.finished ?? meta?.finished ?? false
  // The novel-level judge score (state.novel_score) and its peak. The score
  // history below is a different thing: per-attempt rows across every phase.
  const novelScore = runState?.novelScore ?? meta?.novelScore ?? null
  const bestNovel = runState?.bestNovelScore ?? meta?.bestNovelScore ?? null
  const lastKept = [...scores].reverse().find((s) => s.kept)
  const hasProgress = finished
    || (runState?.chaptersDone ?? 0) > 0
    || (runState?.foundationScore ?? 0) > 0
    || (runState?.phase && runState.phase !== 'foundation' && runState.phase !== 'idle')
  const startLabel = hasProgress ? 'resume run' : 'start run'

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {running && (
        <Card className="flex flex-wrap items-center justify-between gap-4 border-accent/40 p-4">
          <div className="flex items-center gap-3">
            <span className="h-2 w-2 animate-pulse bg-accent" />
            <p className="font-mono text-xs text-paper">
              run in progress — phase <span className="text-accent">{runState?.phase ?? '…'}</span>
              {runState?.iteration ? <span className="text-fog-500"> · iter {runState.iteration}</span> : null}
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="accent" onClick={() => navigate(projectRoute(project, 'pipeline'))}>watch the logs ›</Button>
            <Button variant="danger" disabled={stopping}
              onClick={async () => { setStopping(true); await stopRun(); setStopping(false) }}>
              {stopping ? 'stopping…' : 'stop run'}
            </Button>
          </div>
        </Card>
      )}

      {!running && (
        <Card className="flex flex-wrap items-center justify-between gap-4 border-line p-4">
          <div className="min-w-0">
            <p className="font-mono text-xs text-fog-300">
              no live process
              {runState?.exitCode != null && runState.exitCode !== 0 && (
                <span className="text-bad"> · last exit {runState.exitCode}</span>
              )}
            </p>
            <p className="mt-1 font-prose text-[11px] leading-relaxed text-fog-500">
              {hasProgress
                ? 'Resume continues from the current phase in state.json — it does not wipe the project.'
                : 'Start launches the pipeline from foundation. Genre and notes come from this project\'s files.'}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {startError && (
              <p className="max-w-md border border-bad/40 bg-bad/10 px-2 py-1 font-mono text-[11px] text-bad">{startError}</p>
            )}
            <Button variant="accent" disabled={starting}
              onClick={async () => {
                setStarting(true)
                setStartError('')
                try {
                  await resumeRun(project)
                } catch (e) {
                  setStartError(e?.message || String(e))
                } finally {
                  setStarting(false)
                }
              }}>
              {starting ? 'launching…' : `${startLabel} ▸`}
            </Button>
            <Button onClick={() => navigate(projectRoute(project, 'pipeline'))}>pipeline dashboard ›</Button>
          </div>
        </Card>
      )}

      {/* headline numbers */}
      <div className="dock grid grid-cols-2 gap-px xl:grid-cols-4">
        <StatTile label="words" value={meta?.words ? `${(meta.words / 1000).toFixed(1)}k` : '—'}
          sub={meta?.chaptersTotal ? `${meta.chaptersDone ?? 0}/${meta.chaptersTotal} chapters drafted` : 'drafting not started'} />
        <StatTile label="novel score" value={novelScore != null ? Number(novelScore).toFixed(2) : '—'}
          sub={bestNovel != null ? `peak ${Number(bestNovel).toFixed(2)}`
            : scores.length ? `${scores.length} judged attempts` : 'not scored yet'} />
        <StatTile label="foundation" value={runState?.foundationScore ? runState.foundationScore.toFixed(1) : (meta?.foundationScore || '—')}
          sub={runState?.loreScore ? `lore ${Number(runState.loreScore).toFixed(1)}` : 'world + canon gate'} />
        <StatTile label="phase" value={finished ? 'complete' : (runState?.phase ?? meta?.phase ?? '…')}
          sub={finished ? 'all phases done' : running ? 'run active' : 'not started'} accent={running} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.2fr_1fr]">
        <PhaseTracker runState={runState} />

        <div className="space-y-6">
          <Card className="p-5">
            <SectionHead className="mb-3">score history <Hint>Every draft attempt the judge scored. Green bars were kept; red were discarded by the gate.</Hint></SectionHead>
            {scores.length ? (
              <>
                <MiniScores scores={scores} />
                <p className="mt-2 font-mono text-[10px] text-fog-500">
                  last {Math.min(24, scores.length)} attempts · gate at 6.5
                  {lastKept ? ` · latest keep ${lastKept.score.toFixed(2)} (${lastKept.phase})` : ''}
                </p>
              </>
            ) : (
              <p className="font-prose text-sm text-fog-500">
                nothing judged yet. scores appear here as soon as the pipeline starts evaluating drafts.
              </p>
            )}
          </Card>

          <Card className="p-5">
            <SectionHead className="mb-3">inspection tools <Hint>Advanced views you'll only need sometimes — open them from here whenever the question comes up.</Hint></SectionHead>
            <div className="divide-y divide-line border border-line">
              {TOOLS.map((t) => (
                <button key={t.id} onClick={() => navigate(projectRoute(project, t.id))}
                  className="group block w-full p-3 text-left transition-colors hover:bg-ink-850">
                  <p className="font-mono text-xs text-fog-200 group-hover:text-accent">{t.icon} {t.label}</p>
                  <p className="mt-0.5 font-prose text-[11px] leading-relaxed text-fog-500">{t.desc}</p>
                </button>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <Deliverables project={project} />

      {/* sub-view cards */}
      {!meta ? (
        <Skel className="h-28" />
      ) : meta.phase === 'idle' && !meta.chaptersDone ? (
        <EmptyState icon="◈" title="an empty canvas, for now">
          this project hasn't drafted anything yet. once a run starts, the foundation's world bible, the manuscript,
          and every evaluation report will fill in here and in the views on the left.
        </EmptyState>
      ) : (
        <div className="dock grid grid-cols-1 gap-px sm:grid-cols-2">
          {CARDS.map((c) => (
            <button key={c.id} onClick={() => navigate(projectRoute(project, c.id))}
              className="group p-4 text-left transition-colors hover:bg-ink-850">
              <p className="font-mono text-xs text-paper group-hover:text-accent">{c.icon} {c.label}</p>
              <p className="mt-1 font-prose text-[12px] leading-relaxed text-fog-500">{c.desc}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
