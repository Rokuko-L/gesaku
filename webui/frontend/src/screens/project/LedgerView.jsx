import { useEffect, useState } from 'react'
import { api } from '../../api/client.js'
import { useApi } from '../../api/useApi.js'
import { EmptyState, Skel, Unavailable } from '../../components/ui.jsx'

/**
 * Beats & Harvests (inspection tool): premise beats, chapter beat sheets,
 * planned major threads, clustered emergent threads, and open micro-plant
 * callbacks — nothing planted goes unpaid.
 */
export default function LedgerView({ project }) {
  const { data, error, loading, retry } = useApi(() => api.getLedger(project), [project])
  const [showMicro, setShowMicro] = useState(false)

  useEffect(() => {
    document.title = `gesaku · ${project} · ledger`
  }, [project])

  if (loading) return <Skel className="h-[60vh]" />
  if (error) return <Unavailable what="the ledger" error={error} onRetry={retry} />
  const planned = data.plannedThreads ?? []
  const threads = data.threads ?? []
  const callbacks = data.callbacks ?? []
  if (!threads.length && !data.premiseBeats?.length && !planned.length) {
    return (
      <EmptyState icon="⌘" title="no ledger on file">
        once the outline exists, every planted setup and its payoff chapter are tracked here — the promise sheet
        the pipeline holds itself to while drafting.
      </EmptyState>
    )
  }

  const TOTAL = Math.max(
    data.chaptersTotal ?? 0,
    ...threads.map((t) => t.harvest ?? t.planted ?? 0),
    ...planned.map((t) => t.harvest ?? t.planted ?? 0),
    1,
  )

  const threadRow = (t, key) => {
    const paid = t.status === 'paid off'
    const start = (t.planted ?? t.harvest ?? 1) - 1
    const end = paid ? t.harvest : TOTAL
    return (
      <li key={key} className="border-t border-line py-3 first:border-t-0">
        <div className="mb-1.5 flex items-baseline justify-between gap-4">
          <p className="min-w-0 flex-1 truncate text-sm lowercase text-fog-200">
            <span className={`mr-2 ${paid ? 'text-good' : 'text-accent'}`}>
              [{paid ? 'paid' : 'open'}]
            </span>
            {t.thread}
          </p>
          <p className="shrink-0 font-mono text-[11px] text-fog-500">
            {t.planted != null && <>planted ch{t.planted} → </>}
            {paid ? `harvested ch${t.harvest}` : t.planted == null ? `recalled ch${t.harvest}` : 'open'}
          </p>
        </div>
        <div className="relative h-1 bg-ink-800">
          <div
            className={`absolute h-full ${paid ? 'bg-good/60' : 'bg-accent-dim/70'}`}
            style={{ left: `${(start / TOTAL) * 100}%`, width: `${((end - start) / TOTAL) * 100}%` }}
          />
          {t.planted != null && (
            <span className="absolute top-1/2 h-2 w-2 -translate-y-1/2 bg-paper"
              style={{ left: `calc(${(start / TOTAL) * 100}% - 4px)` }} />
          )}
          {paid && (
            <span className="absolute top-1/2 h-2 w-2 -translate-y-1/2 rotate-45 border border-good bg-good"
              style={{ left: `calc(${(end / TOTAL) * 100}% - 4px)` }} />
          )}
        </div>
      </li>
    )
  }

  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-8">
        <p className="section-head">nothing planted goes unpaid</p>
        <h1 className="mt-1 font-display text-2xl font-semibold lowercase tracking-tight text-paper">beats &amp; harvests</h1>
      </header>

      <div className="grid grid-cols-1 gap-10 xl:grid-cols-[1fr_1.4fr]">
        <section className="min-w-0">
          <h2 className="section-head mb-3">premise beats — chapter one</h2>
          {data.premiseBeats?.length ? (
            <ol className="space-y-0">
              {data.premiseBeats.map((b, i) => (
                <li key={i} className={`flex items-baseline gap-3 border-l-2 py-2 pl-4 ${b.done ? 'border-accent' : 'border-ink-600'}`}>
                  <span className="font-mono text-xs text-fog-500">{String(i + 1).padStart(2, '0')}</span>
                  <span className={`text-sm lowercase ${b.done ? 'text-fog-200' : 'text-fog-500'}`}>
                    {b.label}
                    {!b.done && <span className="ml-2 font-mono text-[10px] text-accent">pending</span>}
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="font-mono text-xs text-fog-500">[ no premise beats on file ]</p>
          )}

          <h2 className="section-head mb-3 mt-8">chapter beat sheets</h2>
          {!data.roadmap?.length && (
            <p className="font-mono text-xs text-fog-500">[ no beat sheets on file for this project ]</p>
          )}
          <div className="space-y-4">
            {(data.roadmap ?? []).map((ch) => (
              <div key={ch.chapter} className="border border-line bg-ink-900 p-4">
                <p className="font-mono text-xs text-accent">ch {ch.chapter}</p>
                <p className="mt-0.5 font-prose text-base text-paper">{ch.title}</p>
                <ul className="mt-2 space-y-1">
                  {ch.beats.map((b, i) => (
                    <li key={i} className="flex gap-2 text-xs text-fog-300">
                      <span className="select-none text-fog-500">·</span>{b}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          <h2 className="section-head mb-3 mt-8">
            open callbacks — {callbacks.filter((c) => c.status === 'open').length} open
          </h2>
          <p className="mb-2 font-mono text-[10px] text-fog-500">
            prose-emergent micro-plants (hairbrush, phrase, promise) — optional revision material, never forced into drafts
          </p>
          {callbacks.length ? (
            <ul className="space-y-2 border border-line bg-ink-900 p-3">
              {callbacks.map((c) => (
                <li key={c.id} className="text-xs">
                  <span className={`mr-2 font-mono ${c.status === 'open' ? 'text-accent' : c.status === 'harvested' ? 'text-good' : 'text-fog-500'}`}>
                    [{c.status}]
                  </span>
                  <span className="text-fog-200">{c.text}</span>
                  <span className="ml-2 font-mono text-[10px] text-fog-500">
                    ch{c.sourceChapter}
                    {c.harvestChapter ? ` → ch${c.harvestChapter}` : ''}
                    {c.kind ? ` · ${c.kind}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="font-mono text-xs text-fog-500">[ no emergent callbacks extracted yet ]</p>
          )}
        </section>

        <section className="min-w-0">
          {planned.length > 0 && (
            <>
              <h2 className="section-head mb-3">
                planned major threads — {planned.length} ·{' '}
                {planned.filter((t) => t.status === 'paid off').length} paid ·{' '}
                {planned.filter((t) => t.status !== 'paid off').length} open
              </h2>
              <ul className="mb-8 max-h-[240px] overflow-y-auto overflow-x-hidden border border-line bg-ink-850 px-4">
                {planned.map((t, i) => threadRow(t, `plan-${i}`))}
                <li className="pb-1" aria-hidden="true" />
              </ul>
            </>
          )}

          <h2 className="section-head mb-3">
            foreshadowing ledger — {threads.length} tracked ·{' '}
            {threads.filter((t) => t.status === 'paid off').length} paid ·{' '}
            {threads.filter((t) => t.status !== 'paid off').length} open
          </h2>
          <button
            type="button"
            className="mb-2 font-mono text-[10px] text-accent underline-offset-2 hover:underline"
            onClick={() => setShowMicro((v) => !v)}
          >
            {showMicro ? 'hide' : 'show'} all clustered threads
          </button>
          <ul className="max-h-[480px] overflow-y-auto overflow-x-hidden border border-line bg-ink-850 px-4">
            {(showMicro ? threads : threads.filter((t) => t.status !== 'paid off')).map((t, i) =>
              threadRow(t, `thr-${i}`),
            )}
            <li className="pb-1" aria-hidden="true" />
          </ul>
          <div className="mt-3 flex items-center gap-4 font-mono text-[10px] text-fog-500">
            <span>● plant</span>
            <span><span className="mr-1 inline-block h-2 w-2 rotate-45 border border-good bg-good align-middle" />harvest</span>
            <span><span className="mr-1 inline-block h-1 w-4 bg-accent-dim/70 align-middle" />open — payoff pending</span>
            <span className="ml-auto">{TOTAL} chapters</span>
          </div>
        </section>
      </div>
    </div>
  )
}
