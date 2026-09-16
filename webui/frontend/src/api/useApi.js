import { useCallback, useEffect, useState } from 'react'

/**
 * Load one API resource with explicit loading / error / data states.
 *
 * Screens used to do `.then(setX).catch(() => {})` with `X` starting as null,
 * which conflates "still loading" with "the bridge failed" — the UI then sat on
 * a skeleton forever, or (worse) showed a fixture. This hook keeps the failure
 * distinguishable so the screen can say so.
 *
 * @param {() => Promise<any>} loader
 * @param {Array} deps re-load triggers (usually [project])
 */
export function useApi(loader, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const [nonce, setNonce] = useState(0)
  const retry = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    setState({ data: null, error: null, loading: true })
    Promise.resolve()
      .then(loader)
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false })
      })
      .catch((error) => {
        if (!cancelled) setState({ data: null, error, loading: false })
      })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { ...state, retry }
}
