import { useState } from 'react'

/**
 * Shared UI primitives — the design system for the console.
 * Visual language: warm-black ink surfaces, cream fog text, one ember accent,
 * mono for machine data, serif (font-prose) for novel text.
 */

/** Collapsible info tooltip — inline onboarding without modal interruption. */
export function Hint({ children, label = '?', below = false }) {
  const [open, setOpen] = useState(false)
  return (
    <span className={`hint-wrap${below ? ' hint-below' : ''}`}>
      <button
        type="button"
        aria-label="what is this?"
        onClick={() => setOpen(!open)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className="hint-btn"
      >
        {label}
      </button>
      {open && <span className="hint-pop">{children}</span>}
    </span>
  )
}

/** Pipeline phase badge with the phase's canonical color. */
const PHASE_COLOR = {
  foundation: 'text-fog-200 border-ink-600',
  drafting: 'text-accent border-accent/40',
  revision: 'text-good border-good/40',
  export: 'text-fog-300 border-ink-600',
  idle: 'text-fog-500 border-line',
}

export function PhaseBadge({ phase, running }) {
  return (
    <span className={`inline-flex items-center gap-1.5 border px-1.5 py-0.5 font-mono text-[10px] lowercase ${PHASE_COLOR[phase] ?? PHASE_COLOR.idle}`}>
      {running && <span className="h-1.5 w-1.5 animate-pulse bg-accent" />}
      {phase}
    </span>
  )
}

/** Compact score readout: number + 5-segment quality meter. */
export function ScoreSig({ score, label }) {
  if (score == null) {
    return <span className="font-mono text-xs text-fog-500">{label ?? '—'}</span>
  }
  const filled = Math.round((score / 10) * 5)
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`font-mono text-xs ${score >= 6.5 ? 'text-good' : 'text-fog-200'}`}>
        {score.toFixed(1)}
      </span>
      <span className="flex gap-0.5">
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className={`h-3 w-1.5 ${i < filled ? 'bg-accent' : 'bg-ink-600'}`} />
        ))}
      </span>
    </span>
  )
}

/** Section label — the `// heading` editorial mark used across the console. */
export function SectionHead({ children, className = '' }) {
  return <p className={`section-head ${className}`}>{children}</p>
}

/** Standard surface card. */
export function Card({ children, className = '', as: As = 'section' }) {
  return <As className={`border border-line bg-ink-900 ${className}`}>{children}</As>
}

/** Pill tab bar (used by the pipeline dashboard, foundation tabs, …). */
export function TabBar({ tabs, active, onSelect, className = '' }) {
  return (
    <div className={`flex gap-1 border border-line p-1 ${className}`} role="tablist">
      {tabs.map((t) => {
        const id = t.id ?? t
        const label = t.label ?? t
        return (
          <button
            key={id}
            role="tab"
            aria-selected={active === id}
            onClick={() => onSelect(id)}
            className={`px-3 py-1.5 font-mono text-xs transition-colors ${
              active === id ? 'bg-accent text-ink-950' : 'text-fog-400 hover:text-fog-200'
            }`}
          >
            [{label}]
          </button>
        )
      })}
    </div>
  )
}

/**
 * Empty state with onboarding copy: explains what this view becomes once the
 * pipeline fills it, and offers the next action.
 */
export function EmptyState({ icon = '·', title, children, cta }) {
  return (
    <div className="empty-state">
      <p className="font-display text-3xl text-ink-600">{icon}</p>
      <p className="mt-3 font-display text-base lowercase tracking-tight text-fog-300">{title}</p>
      <p className="mx-auto mt-2 max-w-md font-prose text-sm leading-relaxed text-fog-500">{children}</p>
      {cta && <div className="mt-5">{cta}</div>}
    </div>
  )
}

/** Stat tile — designed as a cell of a docked panel grid (no own border). */
export function StatTile({ label, value, sub, accent }) {
  return (
    <div className="p-5">
      <p className="text-xs uppercase tracking-wide text-fog-500">{label}</p>
      <p className={`mt-1 font-mono text-3xl ${accent ? 'text-accent' : 'text-paper'}`}>{value}</p>
      {sub && <p className="mt-1 font-mono text-xs text-fog-500">{sub}</p>}
    </div>
  )
}

/** Sharp 1px-outlined buttons; hover fills ember with dark text. */
export function Button({ children, variant = 'ghost', className = '', ...rest }) {
  const base = 'px-3 py-1.5 font-mono text-xs transition-colors disabled:opacity-40'
  const styles = {
    ghost: 'border border-line text-fog-300 hover:border-accent hover:bg-accent hover:text-ink-950',
    accent: 'border border-accent text-accent hover:bg-accent hover:text-ink-950',
    solid: 'bg-accent text-ink-950 hover:bg-accent-dim',
    danger: 'border border-bad/60 text-bad hover:bg-bad hover:text-ink-950',
  }
  return (
    <button className={`${base} ${styles[variant]} ${className}`} {...rest}>
      {children}
    </button>
  )
}

/** Skeleton placeholder block. */
export function Skel({ className = 'h-24' }) {
  return <div className={`animate-pulse bg-ink-800 ${className}`} />
}

/** Inline markdown — **bold**, *italic*, `code` — enough for LLM-authored prose. */
export function mdInline(text) {
  const parts = String(text ?? '').split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g)
  return parts.map((p, i) => {
    if (/^\*\*[^*]+\*\*$/.test(p)) return <strong key={i}>{p.slice(2, -2)}</strong>
    if (/^\*[^*]+\*$/.test(p)) return <em key={i}>{p.slice(1, -1)}</em>
    if (/^`[^`]+`$/.test(p)) return <code key={i} className="font-mono text-[0.9em] text-accent">{p.slice(1, -1)}</code>
    return p
  })
}

/**
 * Minimal markdown block renderer for the pipeline's document output
 * (world bible, briefs): headings, bullets, rules, quotes — not a general
 * markdown engine.
 */
export function Md({ text, className = '' }) {
  const lines = String(text ?? '').split('\n')
  const out = []
  let list = []
  const flush = (key) => {
    if (list.length) {
      out.push(<ul key={`ul${key}`} className="my-2 space-y-1">{list}</ul>)
      list = []
    }
  }
  lines.forEach((line, i) => {
    const t = line.trim()
    if (/^[-*] /.test(t)) {
      list.push(<li key={i} className="ml-5 list-disc leading-relaxed">{mdInline(t.slice(2))}</li>)
      return
    }
    flush(i)
    if (!t) return
    if (/^(-{3,}|_{3,}|\*{3,})$/.test(t)) {
      out.push(<hr key={i} className="my-4 border-line" />)
    } else if (/^#{1,6} /.test(t)) {
      const level = t.match(/^#+/)[0].length
      // heading text is plain — strip emphasis markers (nested *** defeats the
      // inline parser and headings shouldn't italicize anyway)
      const body = t.replace(/^#+\s*/, '').replace(/\*+/g, '')
      if (level === 1) {
        out.push(<h2 key={i} className="mb-3 mt-6 font-display text-xl font-semibold text-paper first:mt-0">{body}</h2>)
      } else if (level === 2) {
        out.push(<h3 key={i} className="mb-2 mt-6 font-display text-lg font-semibold text-paper">{body}</h3>)
      } else {
        out.push(<h4 key={i} className="mb-1 mt-4 font-mono text-xs uppercase tracking-wide text-fog-300">{body}</h4>)
      }
    } else if (/^> /.test(t)) {
      out.push(<blockquote key={i} className="my-2 border-l-2 border-line pl-3 font-prose italic leading-relaxed text-fog-300">{mdInline(t.slice(2))}</blockquote>)
    } else {
      out.push(<p key={i} className="my-2">{mdInline(t)}</p>)
    }
  })
  flush(lines.length)
  return <div className={className}>{out}</div>
}

/** Relative "time ago" for ISO timestamps; falls back to the raw date. */
export function timeAgo(iso) {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso.slice(0, 10)
  const s = Math.max(0, (Date.now() - then) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
