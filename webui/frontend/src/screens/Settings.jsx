import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client.js'

const ROLES = ['writer', 'judge', 'review']

function Slider({ label, value, min, max, step, format, onChange }) {
  const pct = ((value - min) / (max - min)) * 100
  return (
    <label className="block">
      <span className="mb-1 flex items-baseline justify-between font-mono text-xs">
        <span className="text-fog-400">{label}</span>
        <span className="text-accent">{format(value)}</span>
      </span>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1 w-full cursor-ew-resize appearance-none
          [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:appearance-none
          [&::-webkit-slider-thumb]:rounded-none [&::-webkit-slider-thumb]:bg-accent"
        style={{ background: `linear-gradient(to right, var(--color-accent) ${pct}%, var(--color-ink-600) ${pct}%)` }}
      />
    </label>
  )
}

function Quadrant({ num, label, icon, children }) {
  return (
    <section className="p-6">
      <p className="section-head mb-5">
        <span className="text-accent">[{num}]</span> {label} {icon && <span className="ml-1 text-fog-500">{icon}</span>}
      </p>
      {children}
    </section>
  )
}

export default function Settings() {
  const [settings, setSettings] = useState(null)
  const initial = useRef(null)
  const [saved, setSaved] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    document.title = 'gesaku · settings'
    api.getSettings().then((s) => {
      setSettings(s)
      initial.current = JSON.stringify(s)
    })
  }, [])

  const changedCount = useMemo(() => {
    if (!settings || !initial.current) return 0
    const a = JSON.parse(initial.current)
    let n = 0
    for (const k of Object.keys(a)) if (JSON.stringify(a[k]) !== JSON.stringify(settings[k])) n += 1
    return n
  }, [settings])

  if (!settings) {
    return (
      <div className="grid grid-cols-2 gap-px">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-48 animate-pulse bg-ink-800" />
        ))}
      </div>
    )
  }

  const commit = async () => {
    setSaving(true)
    setError(null)
    try {
      const payload = {
        baseUrl: settings.baseUrl,
        models: settings.models,
        thresholds: settings.thresholds,
        heuristics: settings.heuristics,
        defaults: settings.defaults,
        prices: settings.prices ?? { inputPerMTok: null, outputPerMTok: null },
      }
      if (apiKey.trim()) payload.apiKey = apiKey.trim()
      const next = await api.saveSettings(payload)
      setSettings(next)
      initial.current = JSON.stringify(next)
      setApiKey('')
      setSaved(true)
      setTimeout(() => setSaved(false), 1800)
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setSaving(false)
    }
  }
  const discard = () => {
    setSettings(JSON.parse(initial.current))
    setApiKey('')
    setError(null)
  }

  const input =
    'w-full border border-ink-600 bg-ink-950 px-3 py-2 font-mono text-xs text-fog-200 outline-none focus:border-accent/60'

  const prices = settings.prices ?? { inputPerMTok: null, outputPerMTok: null }
  const setPrice = (key, raw) => {
    const t = String(raw).trim()
    const v = t === '' ? null : Number(t)
    setSettings({ ...settings, prices: { ...prices, [key]: Number.isFinite(v) ? v : null } })
  }

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="section-head">global configuration</p>
          <h1 className="mt-1 flex items-center gap-2 font-display text-2xl font-semibold lowercase tracking-tight text-paper">
            settings
            <span className="inline-block h-3.5 w-2 animate-pulse bg-accent" />
          </h1>
        </div>
        <div className="flex items-center gap-3">
          {changedCount > 0 && (
            <span className="border border-accent/50 px-1.5 py-0.5 font-mono text-[10px] text-accent">[{changedCount} changed]</span>
          )}
          {saved && <span className="font-mono text-[10px] text-good">committed to .env</span>}
          {error && <span className="font-mono text-[10px] text-red-400">{error}</span>}
          <div className="flex gap-2">
            <button
              onClick={discard}
              disabled={!changedCount || saving}
              className="border border-ink-600 px-3 py-1 font-mono text-xs text-fog-400 transition-colors hover:text-fog-200 disabled:opacity-40"
            >
              [ discard ]
            </button>
            <button
              onClick={commit}
              disabled={(!changedCount && !apiKey.trim()) || saving}
              className="border border-accent bg-accent/10 px-3 py-1 font-mono text-xs text-accent transition-colors hover:bg-accent hover:text-ink-950 disabled:opacity-40"
            >
              {saving ? '[ saving… ]' : '[ commit_changes ]'}
            </button>
          </div>
        </div>
      </header>

      <p className="mb-6 max-w-2xl font-prose text-sm leading-relaxed text-fog-400">
        these are global pipeline settings — they apply to every project on the shelf. per-project state
        (scores, drafts, briefs) lives inside each project and is managed from its own views.
      </p>

      <div className="dock grid grid-cols-1 gap-px lg:grid-cols-2">
        <Quadrant num="01" label="api_configuration">
          <div className="space-y-4">
            <label className="block">
              <span className="mb-1 flex items-baseline justify-between font-mono text-[10px]">
                <span className="text-fog-400">endpoint_url</span>
                <span className="text-fog-500">[active: anthropic-dialect]</span>
              </span>
              <input
                className={input}
                value={settings.baseUrl}
                onChange={(e) => setSettings({ ...settings, baseUrl: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="mb-1 flex items-baseline justify-between font-mono text-[10px]">
                <span className="text-fog-400">auth_token</span>
                <span className="text-fog-500">{apiKey ? '[new — write on commit]' : `[${settings.apiKeyMasked || 'empty'}]`}</span>
              </span>
              <input
                className={input}
                type="password"
                autoComplete="off"
                placeholder="paste new key to replace stored value"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </label>
            <p className="border-l-2 border-accent/60 bg-accent/5 px-3 py-2 font-mono text-[11px] leading-relaxed text-fog-300">
              to route workloads to another provider (deepseek, openrouter, ollama), override endpoint_url to
              its /v1/chat/completions base and update the token. that endpoint applies to every role;
              per-role routing is not exposed here (set GESAKU_&lt;ROLE&gt;_PROVIDER in .env).
            </p>
            <div className="border-t border-line pt-4">
              <p className="mb-2 font-mono text-[10px] text-fog-400">cost_estimation — optional</p>
              <div className="flex gap-3">
                <label className="flex-1">
                  <span className="mb-1 block font-mono text-[10px] text-fog-500">input $ / 1M tok</span>
                  <input
                    className={input}
                    type="number" min="0" step="0.01" placeholder="unset"
                    value={prices.inputPerMTok ?? ''}
                    onChange={(e) => setPrice('inputPerMTok', e.target.value)}
                  />
                </label>
                <label className="flex-1">
                  <span className="mb-1 block font-mono text-[10px] text-fog-500">output $ / 1M tok</span>
                  <input
                    className={input}
                    type="number" min="0" step="0.01" placeholder="unset"
                    value={prices.outputPerMTok ?? ''}
                    onChange={(e) => setPrice('outputPerMTok', e.target.value)}
                  />
                </label>
              </div>
              <p className="mt-2 font-mono text-[10px] leading-relaxed text-fog-500">
                leave unset to omit the cost column in telemetry; token counts are always measured.
              </p>
            </div>
          </div>
        </Quadrant>

        <Quadrant num="02" label="model_routing">
          <div className="space-y-4">
            {ROLES.map((role) => (
              <div key={role} className="flex items-end gap-3">
                <label className="flex-1">
                  <span className="mb-1 block font-mono text-[10px] text-fog-400">{role}_model</span>
                  <select
                    className={`${input} appearance-none`}
                    value={settings.models[role]}
                    onChange={(e) =>
                      setSettings({ ...settings, models: { ...settings.models, [role]: e.target.value } })
                    }
                  >
                    {[...new Set([settings.models[role], 'claude-sonnet-4-6', 'claude-opus-4-6', 'claude-haiku-4-5'])].map((m) => (
                      <option key={m}>{m}</option>
                    ))}
                  </select>
                </label>
                <label className="w-20">
                  <span className="mb-1 block text-center font-mono text-[10px] text-fog-500">temp</span>
                  <input
                    className={`${input} text-center`}
                    value={{ writer: '0.85', judge: '0.20', review: '0.60' }[role]}
                    readOnly
                  />
                </label>
              </div>
            ))}
          </div>
        </Quadrant>

        <Quadrant num="03" label="heuristics" icon="≡">
          <div className="space-y-5">
            <Slider
              label="foundation_threshold" min={0} max={10} step={0.1}
              value={settings.thresholds.foundation}
              format={(v) => v.toFixed(1)}
              onChange={(v) => setSettings({ ...settings, thresholds: { ...settings.thresholds, foundation: v } })}
            />
            <Slider
              label="chapter_threshold" min={0} max={10} step={0.1}
              value={settings.thresholds.chapter}
              format={(v) => v.toFixed(1)}
              onChange={(v) => setSettings({ ...settings, thresholds: { ...settings.thresholds, chapter: v } })}
            />
            <Slider
              label="max_chapter_attempts" min={1} max={10} step={1}
              value={settings.heuristics.maxChapterAttempts}
              format={(v) => String(v)}
              onChange={(v) => setSettings({ ...settings, heuristics: { ...settings.heuristics, maxChapterAttempts: v } })}
            />
            <Slider
              label="revision_cycles" min={0} max={6} step={1}
              value={settings.heuristics.revisionCycles}
              format={(v) => String(v)}
              onChange={(v) => setSettings({ ...settings, heuristics: { ...settings.heuristics, revisionCycles: v } })}
            />
            <Slider
              label="plateau_delta" min={0} max={0.2} step={0.01}
              value={settings.heuristics.plateauDelta}
              format={(v) => v.toFixed(2)}
              onChange={(v) => setSettings({ ...settings, heuristics: { ...settings.heuristics, plateauDelta: v } })}
            />
          </div>
        </Quadrant>

        <Quadrant num="04" label="defaults">
          <div className="space-y-4">
            <label className="block">
              <span className="mb-1 block font-mono text-[10px] text-fog-400">default_genre</span>
              <input
                className={input}
                placeholder="e.g. comedy fantasy misunderstanding"
                value={settings.defaults.genre}
                onChange={(e) => setSettings({ ...settings, defaults: { ...settings.defaults, genre: e.target.value } })}
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-[10px] text-fog-400">chapter_count</span>
              <input
                type="number" min={4} max={60}
                className={input}
                value={settings.defaults.chapterCount}
                onChange={(e) =>
                  setSettings({ ...settings, defaults: { ...settings.defaults, chapterCount: Number(e.target.value) } })
                }
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-[10px] text-fog-400">notes / global_instructions</span>
              <textarea
                rows={4}
                className={`${input} h-28 resize-none`}
                placeholder="enter global directives..."
                value={settings.defaults.notes}
                onChange={(e) => setSettings({ ...settings, defaults: { ...settings.defaults, notes: e.target.value } })}
              />
            </label>
          </div>
        </Quadrant>
      </div>
    </div>
  )
}
