import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client.js'
import { navigate, projectRoute } from '../../router.js'
import { EmptyState, Md, Skel, Unavailable } from '../../components/ui.jsx'
import EntityGraph from '../../components/EntityGraph.jsx'

const TABS = [
  { id: 'graph', label: 'entity_graph' },
  { id: 'world', label: 'world_bible' },
  { id: 'characters', label: 'characters' },
  { id: 'canon', label: 'canon' },
  { id: 'voice', label: 'voice' },
]

/**
 * Foundation view: everything the pipeline believes to be true about this
 * novel — the entity graph plus the world bible, character registry, canon
 * rules, and voice doc written during the foundation phase.
 */
export default function FoundationView({ project, tab }) {
  const activeTab = TABS.some((t) => t.id === tab) ? tab : 'graph'
  const [data, setData] = useState(null)          // docs + meta from /api/foundation
  const [graph, setGraph] = useState(null)        // {llm, entities} from /api/entity-graph
  const [arranging, setArranging] = useState(false)
  const [arrangeError, setArrangeError] = useState(null)
  const [sel, setSel] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let cancelled = false
    setData(null)
    setGraph(null)
    setSel(null)
    setArrangeError(null)
    setLoadError(null)
    document.title = `gesaku · ${project} · foundation`
    api.getFoundation(project)
      .then((d) => { if (!cancelled) setData(d ?? {}) })
      .catch((e) => { if (!cancelled) setLoadError(e) })
    // The graph is optional (the heuristic builder can have nothing to draw) —
    // a failure here must not blank the whole view.
    api.getEntityGraph(project)
      .then((g) => { if (!cancelled && g) setGraph(g) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [project, reload])

  const arrange = async () => {
    setArranging(true)
    setArrangeError(null)
    try {
      const g = await api.arrangeEntityGraph(project)
      setGraph(g)
    } catch (e) {
      setArrangeError(e.message)
    } finally {
      setArranging(false)
    }
  }

  const { nodes, edges } = graph?.entities ?? { nodes: [], edges: [] }
  const neighbours = useMemo(() => {
    if (!sel) return []
    return edges
      .filter((e) => e.from === sel.id || e.to === sel.id)
      .map((e) => {
        const otherId = e.from === sel.id ? e.to : e.from
        const other = nodes.find((n) => n.id === otherId)
        return other ? { ...other, rel: e.label } : null
      })
      .filter(Boolean)
  }, [sel, nodes, edges])

  if (loadError) {
    return <Unavailable what="the foundation" error={loadError}
      onRetry={() => setReload((n) => n + 1)} />
  }
  if (!data) return <Skel className="h-[60vh]" />

  const hasFoundation = data.docs ? Object.values(data.docs).some((d) => d?.trim()) || nodes.length > 0 : false
  if (!hasFoundation) {
    return (
      <EmptyState icon="◈" title="the foundation hasn't been written yet">
        during the foundation phase the pipeline invents this novel's world, cast, and canon, scoring each attempt
        against the genre framework. the world bible, character registry, and entity graph will appear here.
      </EmptyState>
    )
  }

  return (
    <div className="-m-6 flex h-[calc(100vh-8.5rem)] flex-col xl:flex-row">
      {/* graph / docs area */}
      <main className="flex min-w-0 flex-1 flex-col p-5">
        <div className="relative min-h-0 flex-1">
          {activeTab === 'graph' && !graph ? (
            <Skel className="h-full" />
          ) : activeTab === 'graph' && !nodes.length ? (
            <EmptyState icon="◉" title="no entity graph yet">
              the graph maps every character, faction, and location and how they entangle. it is built from the
              character registry and world bible once the foundation phase has written them.
            </EmptyState>
          ) : activeTab === 'graph' ? (
            <div className="flex h-full flex-col">
              <div className="absolute left-5 top-5 z-10 flex items-center gap-4 border border-line bg-ink-950/80 px-4 py-2 font-mono text-xs backdrop-blur">
                <span className="text-fog-300"><span className="mr-1.5 text-accent">◉</span>nodes: {nodes.length}</span>
                <span className="text-fog-300"><span className="mr-1.5 text-accent">─</span>edges: {edges.length}</span>
                {graph?.llm && !graph?.stale && (
                  <span className="text-cyan">[ arranged by llm ]</span>
                )}
                {graph?.stale && (
                  <button onClick={arrange} disabled={arranging} title="canon/world changed since this arrangement"
                    className="border border-accent/60 px-2 py-0.5 text-[10px] text-accent transition-colors hover:bg-accent hover:text-ink-950 disabled:opacity-40">
                    {arranging ? 'arranging…' : '[ canon changed — re-arrange ]'}
                  </button>
                )}
                {!graph?.llm && (
                  <button onClick={arrange} disabled={arranging}
                    className="border border-accent/60 px-2 py-0.5 text-[10px] text-accent transition-colors hover:bg-accent hover:text-ink-950 disabled:opacity-40">
                    {arranging ? 'arranging…' : '[ arrange with llm ]'}
                  </button>
                )}
              </div>
              {arrangeError && (
                <p className="absolute right-5 top-5 z-10 max-w-64 border border-bad/60 bg-ink-950/90 px-3 py-2 font-mono text-[10px] leading-relaxed text-bad">
                  {arrangeError}
                </p>
              )}
              <EntityGraph
                nodes={nodes}
                edges={edges}
                arranged={!!graph?.llm}
                onSelect={(d) => setSel(d)}
                className="h-full min-h-[420px] flex-1"
              />
            </div>
          ) : data.docs[activeTab]?.trim() ? (
            <article className="h-full overflow-y-auto border border-line bg-ink-900 p-6 font-prose text-[15px] leading-relaxed text-fog-200">
              <Md text={data.docs[activeTab]} />
            </article>
          ) : (
            <EmptyState icon="◈" title={`the ${activeTab} doc hasn't been written yet`}>
              the foundation phase generates this document while inventing the novel's world and cast.
              it will appear here once the run has written it.
            </EmptyState>
          )}
        </div>

        {/* bottom tab bar */}
        <nav className="mt-3 flex h-10 shrink-0 items-center gap-1 overflow-x-auto">
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => navigate(projectRoute(project, 'foundation', id))}
              className={`h-full border px-4 font-mono text-xs transition-colors ${
                activeTab === id
                  ? 'border-accent/50 bg-accent/10 text-accent'
                  : 'border-line text-fog-400 hover:text-fog-200'
              }`}
            >
              [{label}]
            </button>
          ))}
        </nav>
      </main>

      {/* entity inspector */}
      <aside className={`flex w-80 shrink-0 flex-col border-l border-line bg-ink-900 ${activeTab === 'graph' ? '' : 'hidden xl:flex'}`}>
        {!sel ? (
          <div className="flex flex-1 flex-col items-center justify-center p-8 text-center">
            <p className="font-mono text-xs text-fog-500">entity_inspector</p>
            <p className="mt-2 font-mono text-[11px] leading-relaxed text-fog-500">
              [ click a node to inspect ]
            </p>
          </div>
        ) : (
          <>
            <header className="border-b border-line p-5">
              <p className="section-head">entity_inspector</p>
              <div className="mt-1 flex items-center justify-between gap-2">
                <h2 className="truncate font-display text-lg text-paper">{sel.label}</h2>
                <span className="shrink-0 border border-accent/50 px-1.5 py-0.5 font-mono text-[10px] text-accent">
                  {sel.group ?? sel.kind}
                </span>
              </div>
              {sel.importance != null && (
                <p className="mt-1 font-mono text-[10px] text-fog-500">importance {sel.importance}/10</p>
              )}
              {sel.status && <p className="mt-1 font-mono text-[10px] text-bad">status: {sel.status}</p>}
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              {sel.desc && (
                <section className="mb-5">
                  <p className="mb-1.5 font-mono text-[10px] uppercase tracking-widest text-fog-500">description</p>
                  <p className="font-prose text-[13px] leading-relaxed text-fog-200">{sel.desc}</p>
                </section>
              )}

              {sel.mentions?.length > 0 && (
                <section className="mb-5">
                  <p className="mb-1.5 font-mono text-[10px] uppercase tracking-widest text-fog-500">mentioned_in</p>
                  <div className="flex flex-wrap gap-1.5">
                    {sel.mentions.map((c) => (
                      <button key={c} onClick={() => navigate(projectRoute(project, 'manuscript'))}
                        className="border border-ink-600 px-1.5 py-0.5 font-mono text-[10px] text-fog-400 transition-colors hover:border-accent/50 hover:text-accent">
                        {c}
                      </button>
                    ))}
                  </div>
                </section>
              )}

              {neighbours.length > 0 && (
                <section className="mb-5">
                  <p className="mb-1.5 font-mono text-[10px] uppercase tracking-widest text-fog-500">
                    relationships ({neighbours.length})
                  </p>
                  <ul>
                    {neighbours.map((n) => (
                      <li key={n.id}>
                        <button
                          onClick={() => setSel(n)}
                          className="flex w-full items-center gap-2 py-1 text-left hover:text-accent"
                        >
                          <span className={`h-1.5 w-1.5 ${
                            n.kind === 'character' ? 'bg-accent' : n.kind === 'location' ? 'bg-fog-300' : 'bg-fog-500'
                          }`} />
                          <span className="font-mono text-xs text-fog-200">{n.label}</span>
                          <span className="ml-auto font-mono text-[10px] text-fog-500">{n.rel}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>

            <div className="flex shrink-0 border-t border-line">
              <button
                onClick={() => navigate(projectRoute(project, 'ledger'))}
                className="flex-1 py-3 font-mono text-xs text-fog-300 transition-colors hover:bg-ink-800 hover:text-paper"
              >
                open_ledger
              </button>
              <button
                onClick={() => navigate(projectRoute(project, 'manuscript'))}
                className="flex-1 border-l border-line py-3 font-mono text-xs text-accent transition-colors hover:bg-accent/10"
              >
                read_manuscript
              </button>
            </div>
          </>
        )}
      </aside>
    </div>
  )
}
