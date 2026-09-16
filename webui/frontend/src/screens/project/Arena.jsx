import { useEffect, useState } from 'react'
import { api } from '../../api/client.js'
import { useApi } from '../../api/useApi.js'
import { EmptyState, mdInline, Skel, Unavailable } from '../../components/ui.jsx'

/**
 * Chapter Arena (inspection tool): side-by-side A/B of a discarded draft vs
 * the kept one — the reader plays judge.
 */
export default function Arena({ project }) {
  const { data: matches, error, loading, retry } = useApi(
    () => api.listMatches(project), [project])
  const [idx, setIdx] = useState(0)
  const [pick, setPick] = useState(null) // 'a' | 'tie' | 'b'
  const [history, setHistory] = useState([])

  useEffect(() => {
    document.title = `gesaku · ${project} · arena`
  }, [project])

  if (loading) return <Skel className="h-[60vh]" />
  if (error) return <Unavailable what="the chapter arena" error={error} onRetry={retry} />
  if (!matches.length) {
    return (
      <EmptyState icon="⚔" title="no arena matches">
        the arena pairs each chapter's discarded draft against the kept one. they appear here as soon as the
        pipeline has rejected at least one attempt per chapter.
      </EmptyState>
    )
  }

  const m = matches[idx]
  const total = m.a.elo + m.b.elo
  const wrA = (m.a.elo / total) * 100

  const vote = (choice) => {
    if (pick) return
    setPick(choice)
    const delta = { a: '+12', b: '-12', tie: '±0' }[choice]
    const verdict =
      choice === 'a' ? 'a_defeated_b' : choice === 'b' ? 'b_defeated_a' : 'tie_recorded'
    setHistory((h) => [
      { ts: new Date().toISOString().slice(11, 19), verdict, delta, fresh: true },
      ...h.slice(-19),
    ])
    setTimeout(() => {
      setHistory((h) => h.map((e) => ({ ...e, fresh: false })))
      setPick(null)
      setIdx((i) => (i + 1) % matches.length)
    }, 1600)
  }

  return (
    <div className="-m-6 flex h-[calc(100vh-8.5rem)] flex-col">
      {/* header */}
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line px-2 pb-3">
        <div>
          <p className="font-mono text-xs text-fog-500">match_{String(idx + 1).padStart(3, '0')} // chapter {String(m.chapter).padStart(2, '0')}</p>
          <h1 className="mt-0.5 font-display text-2xl font-semibold lowercase tracking-tight text-paper">
            chapter arena <span className={pick ? 'text-good' : 'text-accent'}>{pick ? '· vote locked' : '· awaiting verdict'}</span>
          </h1>
        </div>
        <div className="w-64 max-w-full">
          <div className="flex justify-between font-mono text-[10px] text-fog-500">
            <span>var_a [{m.a.elo}]</span>
            <span>var_b [{m.b.elo}]</span>
          </div>
          <div className="mt-1 flex h-1.5 divide-x divide-ink-950">
            <div className="bg-accent" style={{ width: `${wrA}%` }} />
            <div className="flex-1 bg-fog-500/60" />
          </div>
          <div className="mt-1 flex justify-between font-mono text-[10px]">
            <span className="text-accent">wr: {wrA.toFixed(1)}%</span>
            <span className="text-fog-400">wr: {(100 - wrA).toFixed(1)}%</span>
          </div>
        </div>
      </header>

      {/* variants */}
      <div className="flex min-h-0 flex-1 flex-col divide-y divide-line md:flex-row md:divide-y-0 md:divide-x">
        {[
          ['variant_a', m.a, pick === 'a'],
          ['variant_b', m.b, pick === 'b'],
        ].map(([label, side, selected]) => (
          <div
            key={label}
            onClick={() => !pick && vote(label === 'variant_a' ? 'a' : 'b')}
            className={`flex min-h-0 flex-1 cursor-default flex-col ${selected ? 'border-t-2 border-accent md:border-t-0' : ''}`}
          >
            <div className={`flex h-9 shrink-0 items-center justify-between border-b px-4 ${
              selected ? 'border-accent/50 bg-ink-900' : 'border-line bg-ink-900'
            }`}>
              <p className={`font-mono text-xs ${selected ? 'text-accent' : 'text-fog-400'}`}>&gt;[{label}]</p>
              <p className="font-mono text-[10px] text-fog-500">
                elo: {side.elo} · ~{side.words}w
              </p>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto bg-ink-950/60">
              <div className="mx-auto max-w-[720px] space-y-5 px-8 py-8 font-prose text-[16px] leading-[1.8] text-fog-200">
                {side.prose.split(/\n\s*\n/).filter((p) => p.trim()).map((p, i) => (
                  <p key={i} className="whitespace-pre-wrap">{mdInline(p.replace(/^#.*\n?/, '').trim())}</p>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* dock */}
      <footer className="shrink-0 border-t border-line bg-ink-900">
        <div className="grid grid-cols-3 divide-x divide-line border-b border-line">
          {[
            ['a_wins', 'select variant a', 'a', 'hover:bg-accent/10 hover:text-accent'],
            ['tie', 'negligible difference', 'tie', 'hover:bg-ink-800 hover:text-paper'],
            ['b_wins', 'select variant b', 'b', 'hover:bg-good/10 hover:text-good'],
          ].map(([label, cap, choice, style]) => (
            <button
              key={choice}
              onClick={() => vote(choice)}
              disabled={!!pick}
              className={`group py-3 text-center font-mono text-sm transition-colors disabled:opacity-50 ${style} ${
                pick === choice ? 'bg-accent/15 text-accent' : 'text-fog-300'
              }`}
            >
              [{label}]
              <span className="ml-2 hidden text-[10px] text-fog-500 sm:inline">{cap}</span>
            </button>
          ))}
        </div>
        <div className="h-20 overflow-y-auto px-6 py-2 font-mono text-[11px] leading-relaxed">
          <p className="float-right text-[10px] text-fog-500">// recent_ops</p>
          {history.length === 0 && <p className="text-fog-500">[ no verdicts yet — pick a winner ]</p>}
          {history.map((h, i) => (
            <p key={i} className={h.fresh ? 'text-accent' : 'text-fog-500'}>
              <span className="mr-2 text-ink-600">{h.ts}</span>
              [sys] {h.verdict} // {h.delta} elo
            </p>
          ))}
        </div>
      </footer>
    </div>
  )
}
