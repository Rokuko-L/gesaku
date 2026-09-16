import { useEffect } from 'react'
import { api } from './api/client.js'
import { useRoute, navigate, projectRoute } from './router.js'
import { AppProvider, useApp } from './state.jsx'
import { Button, EmptyState } from './components/ui.jsx'
import ProjectHeader from './components/ProjectHeader.jsx'
import ProjectsGallery from './screens/ProjectsGallery.jsx'
import Settings from './screens/Settings.jsx'
import Overview from './screens/project/Overview.jsx'
import PipelineDashboard from './screens/project/PipelineDashboard.jsx'
import FoundationView from './screens/project/FoundationView.jsx'
import Manuscript from './screens/project/Manuscript.jsx'
import RevisionView from './screens/project/RevisionView.jsx'
import LedgerView from './screens/project/LedgerView.jsx'
import Arena from './screens/project/Arena.jsx'

/*
 * Information architecture:
 *   top level    → projects (shelf) · workspace · settings   (3 items)
 *   project level→ overview · pipeline · foundation · manuscript · revision
 *                  + contextual tools (beats & harvests, chapter arena)
 */

const PROJECT_NAV = [
  { id: 'overview', label: 'overview' },
  { id: 'pipeline', label: 'pipeline' },
  { id: 'foundation', label: 'foundation' },
  { id: 'manuscript', label: 'manuscript' },
  { id: 'revision', label: 'revision' },
]

const PROJECT_TOOLS = [
  { id: 'ledger', label: 'beats & harvests' },
  { id: 'arena', label: 'chapter arena' },
]

function TopNav({ route }) {
  const { projects, runState } = useApp()
  const active = route.name === 'project' ? route.project : null
  const meta = active ? projects?.find((p) => p.name === active) : null
  // Only show the project nav item once we know the project exists. While the
  // shelf is still loading (projects === null) we cannot tell, so show it then.
  const known = !active || !projects || !!meta
  const running = runState?.running ?? false

  const item = (num, label, target, isActive) => (
    <button
      onClick={() => navigate(target)}
      className={`border-b-2 px-1 pb-2 pt-1 font-mono text-xs transition-colors ${
        isActive ? 'border-accent text-paper' : 'border-transparent text-fog-400 hover:text-fog-200'
      }`}
    >
      {isActive ? '>' : ''}[{num}] {label}
    </button>
  )

  return (
    <nav className="flex shrink-0 items-end gap-5 border-b border-line bg-ink-950 px-5 pt-3">
      <button onClick={() => navigate('/projects')} className="pb-2">
        <span className="font-display text-sm font-semibold tracking-tight text-paper lowercase">
          gesaku<span className="blinker ml-0.5 align-middle" style={{ width: 6, height: 12 }} />
        </span>
      </button>
      {item('01', 'projects', '/projects', route.name === 'projects')}
      {active && known && item('02', meta?.title && meta.title !== 'Untitled' ? meta.title : active,
        projectRoute(active), route.name === 'project')}
      <div className="ml-auto pb-2">
        {item('03', 'settings', '/settings', route.name === 'settings')}
      </div>
    </nav>
  )
}

function Workspace({ route }) {
  const { projects, runState, live } = useApp()
  const project = route.project
  const view = route.view
  const running = runState?.running ?? false

  // An unknown project name must not fall through to the sub-views: they would
  // request a project the bridge doesn't have and render whatever the fallback
  // yields. Say so plainly instead.
  if (projects && !projects.some((p) => p.name === project)) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <main className="min-w-0 flex-1 overflow-y-auto p-6">
          <EmptyState
            icon="✕"
            title="no such project"
            cta={<Button variant="accent" onClick={() => navigate('/projects')}>‹ back to the shelf</Button>}
          >
            there is no project named “{project}” under projects/. it may have been deleted, or this link is stale.
          </EmptyState>
        </main>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ProjectHeader project={project} projects={projects} runState={runState} />
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* second-level nav: phase dashboards + inspection tools */}
        <nav className="flex shrink-0 gap-0.5 overflow-x-auto border-b border-line bg-ink-900/60 px-3 py-2 lg:w-44 lg:flex-col lg:gap-0 lg:overflow-y-auto lg:border-b-0 lg:border-r lg:px-0 lg:py-3">
          {PROJECT_NAV.map(({ id, label }, i) => (
            <button
              key={id}
              onClick={() => navigate(projectRoute(project, id))}
              className={`shrink-0 whitespace-nowrap px-3 py-1.5 text-left font-mono text-xs transition-colors lg:border-l-2 ${
                view === id
                  ? 'border-accent bg-ink-800 text-paper'
                  : 'border-transparent text-fog-400 hover:text-fog-200'
              }`}
            >
              {view === id ? '>' : ''}[{String(i + 1).padStart(2, '0')}] {label}
            </button>
          ))}
          <p className="mt-3 hidden px-3 pb-1 font-mono text-[10px] uppercase tracking-widest text-fog-500 lg:block">
            inspection tools
          </p>
          {PROJECT_TOOLS.map(({ id, label }, i) => (
            <button
              key={id}
              onClick={() => navigate(projectRoute(project, id))}
              className={`shrink-0 whitespace-nowrap px-3 py-1.5 text-left font-mono text-xs transition-colors lg:border-l-2 ${
                view === id
                  ? 'border-accent bg-ink-800 text-paper'
                  : 'border-transparent text-fog-500 hover:text-fog-200'
              }`}
            >
              {view === id ? '>' : ''}[t{i + 1}] {label}
            </button>
          ))}
          <div className="mt-auto hidden px-3 pb-2 lg:block">
            <p className="flex items-center gap-1.5 font-mono text-[10px] text-fog-500">
              <span className={`h-1.5 w-1.5 ${live ? 'bg-good' : 'bg-ink-600'}`} />
              {running ? 'run active' : live ? 'watching' : 'offline'}
            </p>
          </div>
        </nav>

        {/* render as ELEMENTS, never fn calls — fn calls break hook ownership */}
        <main className="min-w-0 flex-1 overflow-y-auto p-6">
          {view === 'overview' && <Overview project={project} />}
          {view === 'pipeline' && <PipelineDashboard project={project} tab={route.tab} />}
          {view === 'foundation' && <FoundationView project={project} tab={route.tab} />}
          {view === 'manuscript' && <Manuscript project={project} />}
          {view === 'revision' && <RevisionView project={project} />}
          {view === 'ledger' && <LedgerView project={project} />}
          {view === 'arena' && <Arena project={project} />}
        </main>
      </div>
    </div>
  )
}

function Shell() {
  const route = useRoute()
  const { setProject } = useApp()

  useEffect(() => {
    if (route.name === 'project') {
      api.setActiveProject(route.project)
      setProject(route.project)
    }
  }, [route.name, route.project, setProject])

  return (
    <div className="flex h-screen flex-col">
      <TopNav route={route} />
      {route.name === 'projects' && <ProjectsGallery />}
      {route.name === 'settings' && (
        <main className="min-w-0 flex-1 overflow-y-auto p-8"><Settings /></main>
      )}
      {route.name === 'project' && <Workspace route={route} key={route.project} />}
    </div>
  )
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  )
}
