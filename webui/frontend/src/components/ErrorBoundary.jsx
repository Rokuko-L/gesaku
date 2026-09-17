import { Component } from 'react'

/**
 * Last-resort boundary for a view that throws on unexpected data.
 *
 * Without one, a single bad payload (`null` where an object was expected)
 * unmounts the entire console to a blank page — which is exactly what a
 * missing ledger used to do.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('view crashed', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="mx-auto max-w-2xl border border-bad/40 bg-ink-900 p-6">
        <p className="section-head text-bad">this view crashed</p>
        <p className="mt-2 font-mono text-xs text-fog-300">
          {String(this.state.error?.message || this.state.error)}
        </p>
        <p className="mt-3 font-mono text-[10px] text-fog-500">
          the rest of the console is still usable — pick another panel in the rail.
        </p>
        <button
          type="button"
          className="mt-4 border border-line px-3 py-1 font-mono text-[11px] text-fog-200 hover:border-accent hover:text-accent"
          onClick={() => this.setState({ error: null })}
        >
          retry
        </button>
      </div>
    )
  }
}
