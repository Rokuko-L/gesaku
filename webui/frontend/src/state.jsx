import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api/client.js'

/**
 * App-wide store: project list, the active project's run state, and the
 * SSE fan-out (log lines + llm events). One EventSource per active project;
 * if the stream fails (bridge down) we degrade to polling run-state every 5s
 * so the header stays truthful.
 */

const AppCtx = createContext(null)

export function AppProvider({ children }) {
  const [projects, setProjects] = useState(null)
  const [runState, setRunState] = useState(null)
  const [live, setLive] = useState(false) // SSE connected?
  const [logLines, setLogLines] = useState([])
  const [llmEvents, setLlmEvents] = useState([])
  const projectRef = useRef(null)
  const [streamEpoch, setStreamEpoch] = useState(0)

  const refreshProjects = useCallback(() => {
    api.listProjects().then(setProjects).catch(() => {})
  }, [])

  useEffect(() => {
    refreshProjects()
  }, [refreshProjects])

  const setProject = useCallback((project) => {
    if (projectRef.current === project) return
    projectRef.current = project
    setStreamEpoch((e) => e + 1) // retriggers the subscription effect
  }, [])

  useEffect(() => {
    const project = projectRef.current
    setLogLines([])
    setLlmEvents([])
    setLive(false)
    if (!project) {
      setRunState(null)
      return undefined
    }
    api.getRunState(project).then(setRunState).catch(() => {})
    let poll = null
    let cancelled = false
    const startPolling = () => {
      poll ??= setInterval(() => {
        api.getRunState(project).then((s) => { if (!cancelled) setRunState(s) }).catch(() => {})
      }, 5000)
    }
    const unsub = api.subscribeStream(project, {
      onState: (s, meta) => {
        if (meta?.streamFailed) {
          // EventSource retries on its own, so leaving it open means the
          // failed stream and the poll both write runState. Close it and
          // degrade to polling only.
          setLive(false)
          unsub()
          startPolling()
          return
        }
        if (s && s.project === project) {
          setLive(true)
          if (poll) { clearInterval(poll); poll = null }  // stream recovered
          setRunState(s)
        }
      },
      onLog: (line) => setLogLines((prev) => [...prev.slice(-400), line]),
      onLlm: (ev) => setLlmEvents((prev) => [...prev.slice(-99), ev]),
    })
    return () => {
      cancelled = true
      unsub()
      if (poll) clearInterval(poll)
    }
  }, [streamEpoch])

  const launchProject = useCallback(async (payload) => {
    const res = await api.createProject(payload)
    refreshProjects()
    return res
  }, [refreshProjects])

  const resumeRun = useCallback(async (project) => {
    const name = project ?? projectRef.current
    if (!name) throw new Error('no project selected')
    const res = await api.startRun(name)
    refreshProjects()
    api.getRunState(name).then(setRunState).catch(() => {})
    return res
  }, [refreshProjects])

  const stopRun = useCallback(async () => {
    const project = projectRef.current
    if (project) await api.stopRun(project)
    api.getRunState(project).then(setRunState).catch(() => {})
  }, [])

  const value = useMemo(() => ({
    projects,
    refreshProjects,
    runState,
    live,
    logLines,
    llmEvents,
    setProject,
    launchProject,
    resumeRun,
    stopRun,
  }), [projects, refreshProjects, runState, live, logLines, llmEvents, setProject, launchProject, resumeRun, stopRun])

  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>
}

export function useApp() {
  return useContext(AppCtx)
}
