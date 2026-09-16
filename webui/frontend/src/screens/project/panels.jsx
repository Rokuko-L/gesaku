import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client.js'
import { useApi } from '../../api/useApi.js'
import { Unavailable } from '../../components/ui.jsx'
import { useApp } from '../../state.jsx'

/**
 * Panels shared by the pipeline dashboard: the evaluation report browser
 * (formerly the Inspector screen) and the LLM event feed.
 */

const ROLE_STYLE = {
  writer: 'bg-accent/10 text-accent',
  judge: 'bg-fog-400/10 text-fog-300',
  review: 'bg-good/10 text-good',
}

function fmt(n) {
  return n == null ? '—' : n.toLocaleString()
}

/* ---------------------------------------------------------------- llm feed */

function EventRow({ ev }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="border-t border-line first:border-t-0">
      <button
        onClick={() => setOpen(!open)}
        className="grid w-full grid-cols-[7rem_5rem_1fr_1fr_5.5rem] items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-ink-800"
      >
        <span className="font-mono text-xs text-fog-500">{ev.ts?.slice(11, 19)}</span>
        <span className={`w-fit px-1.5 py-0.5 font-mono text-xs ${ROLE_STYLE[ev.modelKey]}`}>
          {ev.modelKey}
        </span>
        <span className="font-mono text-sm text-fog-200">
          {ev.ok ? (
            // Only show the token columns when the provider actually reported
            // usage; a row of "↑ — · ↓ —" is noise, not information.
            (ev.tokensIn != null || ev.tokensOut != null) ? (
              <>↑ {fmt(ev.tokensIn)} · ↓ {fmt(ev.tokensOut)}</>
            ) : (
              <span className="text-fog-500">ok</span>
            )
          ) : (
            <span className="text-bad">{ev.error ?? 'failed'}</span>
          )}
          {ev.attempt > 1 && (
            <span className="ml-2 bg-accent/10 px-1 py-0.5 text-[10px] text-accent">
              retry {ev.attempt}
            </span>
          )}
        </span>
        <span className="font-mono text-xs text-fog-500">{(ev.durationMs / 1000).toFixed(1)}s</span>
        <span className={`font-mono text-xs text-right ${open ? 'text-fog-300' : 'text-fog-500'}`}>
          {open ? 'close' : 'prompt'}
        </span>
      </button>
      {open && (
        <div className="border-t border-ink-800 bg-ink-950 px-4 py-3">
          <p className="mb-2 font-mono text-[10px] uppercase tracking-wide text-fog-500">
            prompt head · {fmt(ev.promptChars)} chars sent
          </p>
          <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-fog-300">
            {ev.promptHead}…
          </pre>
        </div>
      )}
    </li>
  )
}

/** LLM call feed: history from the bridge + live rows from the SSE stream. */
export function LlmFeed({ project }) {
  const { llmEvents } = useApp()
  const { data: history, error, loading, retry } = useApi(
    () => api.listLlmEvents(project), [project])

  const events = useMemo(() => {
    const base = history ?? []
    const seenTs = new Set(base.map((e) => e.ts))
    const live = llmEvents.filter((e) => !seenTs.has(e.ts))
    return [...base, ...live]
  }, [history, llmEvents])

  if (loading && !events.length) {
    return (
      <ul className="border border-line bg-ink-900">
        {[0, 1, 2, 3].map((i) => (
          <li key={i} className="h-12 animate-pulse border-t border-line bg-ink-800 first:border-t-0"
            style={{ animationDelay: `${i * 120}ms` }} />
        ))}
      </ul>
    )
  }
  if (error) {
    return <Unavailable what="the llm call feed" error={error} onRetry={retry} />
  }
  if (!events.length) {
    return (
      <p className="border border-line p-10 font-mono text-sm text-fog-500">
        [ no llm calls on record ]
      </p>
    )
  }
  return (
    <ul className="overflow-hidden border border-line bg-ink-900">
      {[...events].reverse().map((ev, i) => (
        <EventRow key={`${ev.ts}-${i}`} ev={ev} />
      ))}
    </ul>
  )
}

/* ----------------------------------------------------------- eval reports */

const DIM_LABEL = {
  voice_adherence: 'voice',
  beat_coverage: 'beats',
  character_voice: 'character',
  prose_quality: 'prose',
  engagement: 'engagement',
  continuity: 'continuity',
  reader_grounding: 'grounding',
}

function DimRow({ name, dim }) {
  const [open, setOpen] = useState(false)
  const weakest = dim.score != null && dim.score < 6.5
  return (
    <li className={weakest ? 'border-l-2 border-bad' : ''}>
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-3 px-3 py-1.5 text-left transition-colors hover:bg-ink-800"
      >
        <span className="w-24 shrink-0 font-mono text-xs text-fog-400">{DIM_LABEL[name] ?? name}</span>
        <span className={`w-10 shrink-0 font-mono text-xs ${weakest ? 'text-bad' : 'text-fog-200'}`}>
          {dim.score?.toFixed(1)}
        </span>
        <div className="h-1.5 flex-1 bg-ink-700">
          <div
            className={`h-full ${weakest ? 'bg-bad/70' : 'bg-accent/70'}`}
            style={{ width: `${(dim.score ?? 0) * 10}%` }}
          />
        </div>
        <span className="w-3 text-[10px] text-fog-500">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-ink-800 bg-ink-950 px-4 py-3 text-xs leading-relaxed">
          {dim.weakestMoment && (
            <p className="font-prose italic text-fog-300">“{dim.weakestMoment}”</p>
          )}
          {dim.fix && <p className="font-mono text-accent">&gt; fix: {dim.fix}</p>}
          {dim.note && (
            <p className="font-mono text-[10px] uppercase tracking-wide text-fog-500">note: {dim.note}</p>
          )}
        </div>
      )}
    </li>
  )
}

/** Full evaluation browser: attempt history per chapter + the report itself. */
export function EvalPane({ project }) {
  const { data: evals, error, loading, retry } = useApi(
    () => api.listEvals(project), [project])

  const groups = useMemo(
    () => (evals
      ? Object.entries(evals)
        .filter(([k]) => k.startsWith('ch'))
        .sort(([a], [b]) => a.localeCompare(b))
      : []),
    [evals],
  )
  const [sel, setSel] = useState(null) // { ch, i }

  useEffect(() => {
    if (!groups.length) return
    const first = groups[0]
    setSel({ ch: first[0], i: first[1].length - 1 })
  }, [groups])

  if (loading) {
    return <div className="h-64 animate-pulse bg-ink-800" />
  }
  if (error) {
    return <Unavailable what="evaluation reports" error={error} onRetry={retry} />
  }
  if (!groups.length) {
    return (
      <p className="border border-line p-10 font-mono text-sm text-fog-500">
        [ no eval logs on disk — reports appear as the judge scores drafts ]
      </p>
    )
  }

  const att = sel ? evals[sel.ch]?.[sel.i] : null
  const openCh = sel?.ch

  return (
    <div className="flex flex-col gap-5 xl:flex-row">
      {/* attempt history */}
      <aside className="w-72 shrink-0">
        <p className="section-head mb-2">// attempt_history</p>
        <ul className="max-h-[520px] overflow-y-auto border border-line bg-ink-900">
          {groups.map(([ch, atts]) => (
            <li key={ch} className="border-t border-line first:border-t-0">
              <div className="flex w-full items-center justify-between px-3 py-2">
                <span className={`font-mono text-xs ${openCh === ch ? 'text-accent' : 'text-fog-300'}`}>
                  [{ch}]
                </span>
                <span className="text-[10px] text-fog-500">{atts.length} tries</span>
              </div>
              <ul className="border-t border-ink-800">
                {atts.map((a, i) => {
                  const active = sel?.ch === ch && sel.i === i
                  return (
                    <li key={a.ts}>
                      <button
                        onClick={() => setSel({ ch, i })}
                        className={`flex w-full items-center justify-between px-3 py-1.5 text-left font-mono text-xs transition-colors ${
                          active ? 'bg-ink-800 text-paper' : 'text-fog-400 hover:text-fog-200'
                        }`}
                      >
                        <span>{active ? '> ' : '  '}{a.ts}</span>
                        <span className="flex items-center gap-2">
                          <span>{a.overall?.toFixed(2)}</span>
                          <span className={`border px-1 text-[10px] ${
                            a.overall >= 6.5 ? 'border-good/40 text-good' : 'border-bad/40 text-bad'
                          }`}>
                            [{a.overall >= 6.5 ? 'keep' : 'discard'}]
                          </span>
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </li>
          ))}
        </ul>
      </aside>

      {/* eval report */}
      <div className="min-w-0 flex-1">
        {!att ? null : (
          <>
            <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 className="font-display text-lg lowercase tracking-tight text-paper">
                  {sel.ch} <span className="text-fog-500">//</span> attempt {sel.i + 1}
                </h2>
                <p className="mt-0.5 font-mono text-xs text-fog-500">
                  overall <span className={att.overall >= 6.5 ? 'text-good' : 'text-bad'}>{att.overall?.toFixed(2)}</span>
                  {' · '}raw judge {att.rawJudge?.toFixed(1)}
                  {' · '}penalties −{(att.slopPenalty + att.lengthPenalty + att.orientationPenalty).toFixed(2)}
                </p>
              </div>
              <div className="flex gap-2 font-mono text-[10px]">
                <span className="border border-bad/40 px-1.5 py-0.5 text-bad">[slop −{att.slopPenalty?.toFixed(2)}]</span>
                <span className="border border-ink-600 px-1.5 py-0.5 text-fog-400">[length −{att.lengthPenalty?.toFixed(2)}]</span>
                <span className="border border-ink-600 px-1.5 py-0.5 text-fog-400">[orient −{att.orientationPenalty?.toFixed(2)}]</span>
              </div>
            </header>

            <section className="mb-5">
              <p className="section-head mb-2">// dimension_scores</p>
              <ul className="overflow-hidden border border-line bg-ink-900">
                {Object.entries(att.dims).map(([k, v]) => (
                  <DimRow key={k} name={k} dim={v} />
                ))}
              </ul>
            </section>

            <section className="mb-5 grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="border border-line bg-ink-900 p-4">
                <p className="section-head mb-2 text-good">// strongest</p>
                <ul className="space-y-2">
                  {att.strongest.map((s, i) => (
                    <li key={i} className="font-prose text-[13px] italic leading-relaxed text-fog-200">{s}</li>
                  ))}
                </ul>
              </div>
              <div className="border border-line border-l-2 border-l-bad bg-ink-900 p-4">
                <p className="section-head mb-2 text-bad">// weakest</p>
                <ul className="space-y-2">
                  {att.weakestSentences.map((s, i) => (
                    <li key={i} className="font-prose text-[13px] italic leading-relaxed text-fog-300">{s}</li>
                  ))}
                </ul>
              </div>
            </section>

            <section className="mb-5">
              <p className="section-head mb-2">// ai_patterns_detected</p>
              <div className="flex flex-wrap gap-2">
                {att.aiPatterns.map((p, i) => (
                  <span key={i} className="border border-ink-600 px-2 py-0.5 font-mono text-[10px] text-fog-400">
                    [{p.length > 60 ? p.slice(0, 57) + '…' : p}]
                  </span>
                ))}
              </div>
            </section>

            <section>
              <p className="section-head mb-2">// top_3_revisions</p>
              <ol className="space-y-2">
                {att.topRevisions.map((r, i) => (
                  <li key={i} className="flex gap-3 text-sm leading-relaxed text-fog-200">
                    <span className="font-display text-lg text-accent">0{i + 1}</span>
                    {r}
                  </li>
                ))}
              </ol>
            </section>
          </>
        )}
      </div>
    </div>
  )
}
