import { api } from '../api/client.js'
import { navigate, projectRoute } from '../router.js'
import { PhaseBadge, ScoreSig, timeAgo } from './ui.jsx'

/**
 * Persistent project context bar — answers "which project am I looking at?"
 * at all times and anchors every project-scoped view. Shows title, genre,
 * word count, phase, best score; doubles as the project switcher.
 */
export default function ProjectHeader({ project, projects, runState }) {
  const meta = projects?.find((p) => p.name === project)
  const phase = runState?.phase ?? meta?.phase ?? '…'
  const running = runState?.running ?? meta?.running ?? false
  const finished = runState?.finished ?? meta?.finished ?? false
  const score = meta?.novelScore || meta?.foundationScore || null

  // Always keep the open project selectable, even if the shelf list is
  // stale/offline — otherwise the browser shows the first option and lies.
  const switchOptions = (() => {
    const list = (projects ?? []).map((p) => ({ name: p.name }))
    if (project && !list.some((p) => p.name === project)) {
      list.unshift({ name: project })
    }
    return list.length ? list : [{ name: project }]
  })()

  const switchTo = (name) => {
    if (!name || name === project) return
    api.setActiveProject(name)
    navigate(projectRoute(name))
  }

  return (
    <header className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-line bg-ink-900 px-5 py-3">
      <button
        onClick={() => navigate('/projects')}
        className="font-mono text-xs text-fog-500 transition-colors hover:text-accent"
        title="back to the shelf"
      >
        ‹ shelf
      </button>

      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="truncate font-display text-lg tracking-tight text-paper">
          {meta ? (meta.title === 'Untitled' ? <span className="text-fog-400">{meta.name}</span> : meta.title) : project}
        </h1>
        <span className="hidden font-mono text-[10px] text-fog-500 sm:inline">{project}</span>
      </div>

      {meta?.genre && (
        <span className="hidden border border-ink-600 px-1.5 py-0.5 font-mono text-[10px] text-fog-400 md:inline">
          {meta.genre}
        </span>
      )}

      <span className="hidden items-center gap-1.5 font-mono text-xs text-fog-400 lg:inline-flex">
        <span className="text-fog-500">words</span>
        {meta?.words ? `${(meta.words / 1000).toFixed(1)}k` : '—'}
        {meta?.chaptersTotal ? <span className="text-fog-500">/ {meta.chaptersTotal}ch</span> : null}
      </span>

      <span className="hidden md:inline-flex"><ScoreSig score={score} label="no score yet" /></span>

      <div className="ml-auto flex items-center gap-3">
        {meta?.updatedAt && (
          <span className="hidden font-mono text-[10px] text-fog-500 xl:inline">upd {timeAgo(meta.updatedAt)}</span>
        )}
        {/* Status sits with the switcher so the two wrap together on a narrow
            header instead of the badge stranding on its own row. */}
        <span className="flex items-center gap-2">
          <PhaseBadge phase={phase} running={running} finished={finished} />
          {running && <span className="font-mono text-[10px] text-accent">live</span>}
        </span>
        <label className="flex items-center gap-1.5 font-mono text-[10px] text-fog-500">
          <span className="sr-only">switch project</span>
          <select
            aria-label="switch project"
            value={project}
            onChange={(e) => switchTo(e.target.value)}
            className="max-w-44 truncate border border-ink-600 bg-ink-950 px-1.5 py-1 font-mono text-[11px] text-fog-300 outline-none focus:border-accent/60"
          >
            {switchOptions.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
        </label>
      </div>
    </header>
  )
}
