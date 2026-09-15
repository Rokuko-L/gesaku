import { useEffect, useMemo, useState } from 'react'
import { useApp } from '../state.jsx'
import { api } from '../api/client.js'
import { navigate, projectRoute } from '../router.js'
import { PhaseBadge, ScoreSig, Button, EmptyState, Hint, Skel, timeAgo } from '../components/ui.jsx'

/**
 * Landing page: the shelf. Project cards surface each project's most
 * important signal (phase, latest score, word count) at a glance; the
 * creation wizard walks a new author from first visit to a launched run
 * in under a minute.
 *
 * Default create flow is the structured "story creator" (character + arcs).
 * A toggle switches back to freeform dump-notes. Optional freeform at the
 * end of the creator attaches to the structured fields — it does not replace
 * them.
 */

const STEPS_CREATOR = ['identity', 'cast', 'arcs', 'shape', 'launch']
const STEPS_DUMP = ['identity', 'premise', 'shape', 'launch']

const MODE_KEY = 'gesaku_story_creator'

/** Person → allowed prose-distance packs (defaults are the least tick-prone). */
const PROSE_MODES = {
  first_person: [
    { id: 'first_voicey', label: 'voicey', hint: 'personality-forward teller; scene still leads' },
    { id: 'first_intimate', label: 'intimate', hint: 'close stream-of-consciousness; live inner voice' },
  ],
  third_person: [
    { id: 'third_close', label: 'close limited', hint: 'one head; thought bleeds into narration' },
    { id: 'third_scene', label: 'scene-first', hint: 'cinematography and action; sparse interiority' },
  ],
}
const DEFAULT_PROSE = { first_person: 'first_voicey', third_person: 'third_close' }

const AWARENESS = [
  { id: 'unaware', label: 'unaware', hint: 'does not know the secret' },
  { id: 'suspects', label: 'suspects', hint: 'has doubts, no proof' },
  { id: 'almost knows', label: 'almost knows', hint: 'on the edge of the truth' },
  { id: 'fully aware', label: 'fully aware', hint: 'knows the real situation' },
]

const emptyCast = () => ({ id: crypto.randomUUID(), name: '', role: '', awareness: 'unaware' })
const emptyArc = (i) => ({
  id: crypto.randomUUID(),
  title: ['setup', 'body', 'endgame'][i] ?? `arc ${i + 1}`,
  beats: '',
})

const SEG = {
  wrapper: 'flex border border-ink-600',
  btn: (active) =>
    `flex-1 px-3 py-1.5 font-mono text-xs transition-colors ${
      active ? 'bg-accent text-ink-950' : 'text-fog-400 hover:text-fog-200'
    }`,
}

/** Serialize structured story-creator fields into notes-template markdown. */
export function serializeStoryNotes(form) {
  const lines = []
  lines.push('Logline:')
  lines.push(form.logline.trim() || '(premise from character and arcs below)')
  lines.push('')
  if (form.genre.trim()) lines.push(`Genre: ${form.genre.trim()}`)
  if (form.tone.trim()) lines.push(`Tone: ${form.tone.trim()}`)
  if (form.workingTitle.trim()) lines.push(`Working title: ${form.workingTitle.trim()}`)
  lines.push('')

  lines.push('1. The World')
  lines.push(form.worldHint.trim() || '(derive from genre, logline, and cast — invent setting details as needed)')
  lines.push('')

  lines.push('2. Main Characters')
  const proto = form.protagonistName.trim() || 'Protagonist'
  lines.push(
    `[Protagonist] ${proto}: ${form.protagonistGist.trim() || '(public role vs what is really going on — gist TBD)'}`,
  )
  for (const c of form.cast) {
    if (!c.name.trim() && !c.role.trim()) continue
    const label = c.name.trim() || 'Unnamed'
    lines.push(`- ${label}: ${c.role.trim() || '(role TBD)'} — awareness: ${c.awareness}`)
  }
  lines.push('Minor / supporting characters: generate as needed from the above.')
  lines.push('')

  lines.push('3. High-Level Outline (Arcs)')
  form.arcs.forEach((a, i) => {
    const title = a.title.trim() || `Arc ${i + 1}`
    lines.push(`Arc ${i + 1}: ${title}`)
    if (a.beats.trim()) lines.push(a.beats.trim())
    else lines.push('(beats TBD — invent a coherent movement for this arc)')
    lines.push('')
  })

  if (form.hiddenTruth.trim() || form.revealChapter) {
    lines.push('4. Hidden Truth / Reveal Schedule')
    lines.push(form.hiddenTruth.trim() || '(truth not written — do not invent a twist unless implied by arcs)')
    if (form.revealChapter) {
      lines.push(
        `Intended reveal: around chapter ${form.revealChapter}. Seal this in foundation canon as visible_from=${form.revealChapter}; early chapters must not state it.`,
      )
    }
    lines.push('')
  }

  if (form.extraNotes.trim()) {
    lines.push('Additional freeform notes (author attach — not a replacement for the fields above):')
    lines.push(form.extraNotes.trim())
    lines.push('')
  }

  return lines.join('\n').trim() + '\n'
}

function ProjectCard({ p }) {
  const progress = p.chaptersTotal ? Math.round((p.chaptersDone / p.chaptersTotal) * 100) : 0
  return (
    <button
      className="group w-full p-5 text-left transition-colors hover:bg-ink-850"
      onClick={() => {
        api.setActiveProject(p.name)
        navigate(projectRoute(p.name))
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-display text-base tracking-tight text-paper group-hover:text-accent">
            {p.title === 'Untitled' ? <span className="text-fog-400">{p.name}</span> : p.title}
          </p>
          <p className="mt-0.5 truncate font-mono text-[10px] text-fog-500">{p.name}</p>
        </div>
        <PhaseBadge phase={p.phase} running={p.running} />
      </div>

      {p.genre && <p className="mt-2 truncate font-prose text-xs italic text-fog-400">{p.genre}</p>}

      <div className="mt-4 flex items-center justify-between">
        <ScoreSig score={p.novelScore || p.foundationScore || null} label="unscored" />
        <span className="font-mono text-xs text-fog-400">
          {p.words ? `${(p.words / 1000).toFixed(1)}k words` : 'no prose yet'}
        </span>
      </div>

      {p.chaptersTotal > 0 && (
        <div className="mt-3">
          <div className="flex justify-between font-mono text-[10px] text-fog-500">
            <span>ch {p.chaptersDone}/{p.chaptersTotal}</span>
            <span>{progress}%</span>
          </div>
          <div className="mt-1 h-1 bg-ink-700">
            <div className="h-full bg-accent/70 transition-all" style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}

      <p className="mt-3 flex items-center justify-between font-mono text-[10px] text-fog-500">
        <span>upd {timeAgo(p.updatedAt)}</span>
        <span className="text-fog-500 transition-colors group-hover:text-accent">open ›</span>
      </p>
    </button>
  )
}

function CastRow({ c, onChange, onRemove, canRemove }) {
  return (
    <div className="flex flex-wrap items-end gap-2 border border-line p-2">
      <label className="min-w-0 flex-1">
        <span className="field-label">name</span>
        <input
          className="field-input"
          placeholder="e.g. Mira"
          value={c.name}
          onChange={(e) => onChange({ ...c, name: e.target.value })}
        />
      </label>
      <label className="min-w-0 flex-[2]">
        <span className="field-label">role / gist</span>
        <input
          className="field-input"
          placeholder="e.g. court archivist who notices the forgeries"
          value={c.role}
          onChange={(e) => onChange({ ...c, role: e.target.value })}
        />
      </label>
      <label className="min-w-0 flex-1">
        <span className="field-label">awareness</span>
        <select
          className="field-input"
          value={c.awareness}
          onChange={(e) => onChange({ ...c, awareness: e.target.value })}
        >
          {AWARENESS.map((a) => (
            <option key={a.id} value={a.id}>{a.label}</option>
          ))}
        </select>
      </label>
      {canRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="pb-2 font-mono text-xs text-fog-500 hover:text-bad"
          title="remove"
        >
          ✕
        </button>
      )}
    </div>
  )
}

function Wizard({ onClose, onLaunch }) {
  const [mode, setMode] = useState(() => {
    const saved = localStorage.getItem(MODE_KEY)
    // default: story creator on
    return saved === '0' ? 'dump' : 'creator'
  })
  const [step, setStep] = useState(0)
  const [form, setForm] = useState({
    // identity
    name: '', // folder / project id — NOT the novel title
    workingTitle: '',
    genre: '', tone: '',
    // dump mode
    notes: '', notesPath: '', notesMode: 'text',
    // creator: story
    logline: '',
    worldHint: '',
    protagonistName: '', protagonistGist: '',
    cast: [emptyCast(), emptyCast()],
    // creator: arcs
    arcs: [emptyArc(0), emptyArc(1), emptyArc(2)],
    // creator: reveal (optional)
    hiddenTruth: '', revealChapter: '',
    // creator: freeform attach (optional, appended)
    extraNotes: '',
    // shape
    chapters: 24, chaptersCustom: false,
    wordsPerChapter: 3000, wordsCustom: false,
    revisionCycles: 3, perspective: 'third_person', proseMode: 'third_close',
  })
  const [error, setError] = useState(null)
  const [launching, setLaunching] = useState(false)

  const steps = mode === 'creator' ? STEPS_CREATOR : STEPS_DUMP

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))
  const setModePersist = (m) => {
    setMode(m)
    localStorage.setItem(MODE_KEY, m === 'creator' ? '1' : '0')
    setStep(0)
  }

  const notesPreview = useMemo(
    () => (mode === 'creator' ? serializeStoryNotes(form) : form.notes),
    [mode, form],
  )
  const notesWordCount = notesPreview.trim() ? notesPreview.trim().split(/\s+/).filter(Boolean).length : 0

  const stepValid = [
    form.name.trim() && form.genre.trim(),
    mode === 'creator'
      ? (form.protagonistName.trim() || form.protagonistGist.trim() || form.logline.trim())
      : (form.notesMode === 'path' ? form.notesPath.trim() : true),
    true,
    true,
    true,
  ][step] ?? true

  const launch = async () => {
    setLaunching(true)
    setError(null)
    try {
      const notes = mode === 'creator' ? serializeStoryNotes(form) : form.notes
      await onLaunch({ ...form, notes, creatorMode: mode })
    } catch (e) {
      setError(e.message)
      setLaunching(false)
    }
  }

  const patchCast = (id, next) =>
    setForm((f) => ({ ...f, cast: f.cast.map((c) => (c.id === id ? next : c)) }))
  const patchArc = (id, next) =>
    setForm((f) => ({ ...f, arcs: f.arcs.map((a) => (a.id === id ? next : a)) }))

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/80 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="flex max-h-[92vh] w-full max-w-xl flex-col overflow-x-hidden border border-ink-600 bg-ink-900"
        onClick={(e) => e.stopPropagation()}
      >

        {/* Header + mode strip are outside the scroll body so the Hint
            popover (absolute, no z-index) is never clipped by overflow-y. */}
        <header className="shrink-0 border-b border-line px-6 py-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="section-head">initiate a novel</p>
              <p className="mt-0.5 font-mono text-[10px] text-fog-500">
                step {step + 1}/{steps.length} · {steps[step]}
              </p>
            </div>
            <button onClick={onClose} className="font-mono text-xs text-fog-500 hover:text-fog-200">✕</button>
          </div>
          <div className="mt-3">
            <div className={SEG.wrapper}>
              <button
                type="button"
                onClick={() => setModePersist('creator')}
                className={SEG.btn(mode === 'creator')}
                title="guided fields: character, cast, arcs — serialized into seed notes"
              >
                [story creator]
              </button>
              <button
                type="button"
                onClick={() => setModePersist('dump')}
                className={SEG.btn(mode === 'dump')}
                title="paste a full premise dump or point at a notes file"
              >
                [paste notes]
              </button>
            </div>
            <p className="mt-1.5 font-mono text-[10px] leading-relaxed text-fog-500">
              {mode === 'creator'
                ? 'structured quickstart (default). freeform at the end attaches to these fields — it does not replace them.'
                : 'classic dump mode: one freeform notes box (or file path) becomes the seed as-is.'}
            </p>
          </div>
          <div className="mt-3 flex gap-0.5">
            {steps.map((s, i) => (
              <button
                key={s}
                onClick={() => i < step && setStep(i)}
                title={s}
                className={`h-1 flex-1 transition-colors ${i <= step ? 'bg-accent' : 'bg-ink-700'} ${i < step ? 'cursor-pointer' : ''}`}
              />
            ))}
          </div>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 pb-5 pt-5">
          {steps[step] === 'identity' && (
            <>
              <label className="block">
                <span className="field-label">
                  project id <Hint below>Folder name under projects/. Not the novel's display title — that is generated later (or set below as a working title).</Hint>
                </span>
                <input
                  className="field-input"
                  placeholder="e.g. orient-express-wip  (folder only)"
                  value={form.name}
                  onChange={set('name')}
                  autoFocus
                />
                <span className="mt-1 block font-mono text-[10px] text-fog-500">
                  this is only the on-disk folder / project key. the novel title is invented by the pipeline.
                </span>
              </label>
              {mode === 'creator' && (
                <label className="block">
                  <span className="field-label">
                    working title <Hint below>Optional. A human label for you — does not replace the pipeline-generated novel title.</Hint>
                  </span>
                  <input
                    className="field-input"
                    placeholder="e.g. Twelve Winters on the Snow Line"
                    value={form.workingTitle}
                    onChange={set('workingTitle')}
                  />
                </label>
              )}
              <label className="block">
                <span className="field-label">
                  genre classification <Hint below>The genre steers the whole foundation pass — world, tone, and the story engine. Free text works best.</Hint>
                </span>
                <input
                  className="field-input"
                  placeholder="e.g. closed-circle fair-play whodunnit"
                  value={form.genre}
                  onChange={set('genre')}
                />
              </label>
              {mode === 'creator' && (
                <label className="block">
                  <span className="field-label">tone (optional)</span>
                  <input
                    className="field-input"
                    placeholder="e.g. dry wit, snow-bright dread, no gore"
                    value={form.tone}
                    onChange={set('tone')}
                  />
                </label>
              )}
              <div>
                <span className="field-label">narration perspective</span>
                <div className={SEG.wrapper}>
                  {['third_person', 'first_person'].map((pv) => (
                    <button
                      key={pv}
                      onClick={() => setForm({ ...form, perspective: pv, proseMode: DEFAULT_PROSE[pv] })}
                      className={SEG.btn(form.perspective === pv)}
                    >
                      {pv}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <span className="field-label">
                  prose mode <Hint>How close the camera sits to the POV. Changes drafting rhythm, not plot.</Hint>
                </span>
                <div className={SEG.wrapper}>
                  {(PROSE_MODES[form.perspective] || []).map((m) => (
                    <button
                      key={m.id}
                      onClick={() => setForm({ ...form, proseMode: m.id })}
                      className={SEG.btn(form.proseMode === m.id)}
                      title={m.hint}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* ——— creator: cast ——— */}
          {mode === 'creator' && steps[step] === 'cast' && (
            <>
              <label className="block">
                <span className="field-label">
                  logline <Hint>One sentence: who, what goes wrong, why it matters.</Hint>
                </span>
                <textarea
                  rows={2}
                  className="field-input resize-none"
                  placeholder="A stranded detective must solve a locked-room murder before the train is dug out of the snow…"
                  value={form.logline}
                  onChange={set('logline')}
                />
              </label>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="field-label">main character — name</span>
                  <input
                    className="field-input"
                    placeholder="e.g. Inspector Vale"
                    value={form.protagonistName}
                    onChange={set('protagonistName')}
                  />
                </label>
                <label className="block">
                  <span className="field-label">
                    their gist <Hint>Public role vs what is really going on (mask, con, secret, flaw).</Hint>
                  </span>
                  <input
                    className="field-input"
                    placeholder="e.g. retired inspector hiding a heart condition"
                    value={form.protagonistGist}
                    onChange={set('protagonistGist')}
                  />
                </label>
              </div>
              <label className="block">
                <span className="field-label">world / setting hint (optional)</span>
                <textarea
                  rows={2}
                  className="field-input resize-none"
                  placeholder="1930s mountain railway, sealed snowdrift, twelve passengers who all knew the victim…"
                  value={form.worldHint}
                  onChange={set('worldHint')}
                />
              </label>
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <span className="field-label">
                    main cast <Hint>Only the majors. Minor characters are generated later.</Hint>
                  </span>
                  <button
                    type="button"
                    className="font-mono text-[10px] text-accent hover:underline"
                    onClick={() => setForm((f) => ({ ...f, cast: [...f.cast, emptyCast()] }))}
                  >
                    + add character
                  </button>
                </div>
                <div className="space-y-2">
                  {form.cast.map((c) => (
                    <CastRow
                      key={c.id}
                      c={c}
                      canRemove={form.cast.length > 1}
                      onChange={(next) => patchCast(c.id, next)}
                      onRemove={() => setForm((f) => ({ ...f, cast: f.cast.filter((x) => x.id !== c.id) }))}
                    />
                  ))}
                </div>
              </div>
            </>
          )}

          {/* ——— creator: arcs ——— */}
          {mode === 'creator' && steps[step] === 'arcs' && (
            <>
              <p className="font-mono text-[10px] leading-relaxed text-fog-500">
                break the plot into arcs. each arc becomes a movement of the outline — 3–6 beats per arc is enough.
                write story events, not chapter numbers.
              </p>
              {form.arcs.map((a, i) => (
                <div key={a.id} className="border border-line p-3">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="font-mono text-[10px] text-fog-500">arc {i + 1}</span>
                    <input
                      className="field-input flex-1"
                      placeholder="arc title (setup / body / endgame…)"
                      value={a.title}
                      onChange={(e) => patchArc(a.id, { ...a, title: e.target.value })}
                    />
                    {form.arcs.length > 1 && (
                      <button
                        type="button"
                        className="font-mono text-xs text-fog-500 hover:text-bad"
                        onClick={() => setForm((f) => ({ ...f, arcs: f.arcs.filter((x) => x.id !== a.id) }))}
                      >
                        ✕
                      </button>
                    )}
                  </div>
                  <textarea
                    rows={3}
                    className="field-input resize-none"
                    placeholder={'one beat per line\nthe body is discovered at dawn\nalibis interlock too neatly'}
                    value={a.beats}
                    onChange={(e) => patchArc(a.id, { ...a, beats: e.target.value })}
                  />
                </div>
              ))}
              <button
                type="button"
                className="font-mono text-[10px] text-accent hover:underline"
                onClick={() => setForm((f) => ({ ...f, arcs: [...f.arcs, emptyArc(f.arcs.length)] }))}
              >
                + new arc
              </button>

              <details className="border border-line">
                <summary className="cursor-pointer px-3 py-2 font-mono text-[10px] text-fog-400 hover:text-fog-200">
                  optional · hidden truth / reveal (for sealed foundation)
                </summary>
                <div className="space-y-3 border-t border-line p-3">
                  <label className="block">
                    <span className="field-label">
                      what is really going on <Hint>Written for the pipeline, not for the early chapters. Used to tag foundation canon as sealed.</Hint>
                    </span>
                    <textarea
                      rows={3}
                      className="field-input resize-none"
                      placeholder="all twelve passengers share guilt for the old case; no single killer"
                      value={form.hiddenTruth}
                      onChange={set('hiddenTruth')}
                    />
                  </label>
                  <label className="block max-w-40">
                    <span className="field-label">reveal around chapter</span>
                    <input
                      type="number"
                      min={2}
                      max={form.chapters}
                      className="field-input"
                      placeholder="e.g. 18"
                      value={form.revealChapter}
                      onChange={(e) => setForm((f) => ({ ...f, revealChapter: e.target.value }))}
                    />
                  </label>
                </div>
              </details>
            </>
          )}

          {/* ——— dump: premise ——— */}
          {mode === 'dump' && steps[step] === 'premise' && (
            <>
              <div className={SEG.wrapper}>
                {[['text', 'paste premise'], ['path', 'use a file']].map(([id, label]) => (
                  <button key={id} onClick={() => setForm({ ...form, notesMode: id })} className={SEG.btn(form.notesMode === id)}>
                    [{label}]
                  </button>
                ))}
              </div>
              {form.notesMode === 'text' ? (
                <label className="block">
                  <span className="field-label">
                    premise / seed notes <Hint>Full dump. Long notes are fine: summarized for the genre framework, kept in full as the seed.</Hint>
                  </span>
                  <textarea
                    rows={10}
                    className="field-input resize-none"
                    placeholder="A stuck archivist discovers the royal library is quietly rewriting history…"
                    value={form.notes}
                    onChange={set('notes')}
                  />
                </label>
              ) : (
                <label className="block">
                  <span className="field-label">path to an existing notes file</span>
                  <input className="field-input" placeholder="./notes/premise.txt" value={form.notesPath} onChange={set('notesPath')} />
                </label>
              )}
            </>
          )}

          {steps[step] === 'shape' && (
            <>
              <div>
                <span className="field-label">chapters</span>
                <div className={SEG.wrapper}>
                  {[12, 24, 30, 48].map((n) => (
                    <button key={n} onClick={() => setForm({ ...form, chapters: n, chaptersCustom: false })}
                      className={SEG.btn(form.chapters === n && !form.chaptersCustom)}>{n}</button>
                  ))}
                  <button onClick={() => setForm({ ...form, chaptersCustom: true })}
                    className={SEG.btn(form.chaptersCustom)}>custom</button>
                </div>
                {form.chaptersCustom && (
                  <input type="number" min={4} max={200}
                    className="field-input mt-2 max-w-32" value={form.chapters}
                    onChange={(e) => setForm({ ...form, chapters: Math.max(4, Math.min(200, Number(e.target.value) || 0)) })} />
                )}
              </div>
              <div>
                <span className="field-label">words per chapter</span>
                <div className={SEG.wrapper}>
                  {[2000, 3000, 4000].map((n) => (
                    <button key={n} onClick={() => setForm({ ...form, wordsPerChapter: n, wordsCustom: false })}
                      className={SEG.btn(form.wordsPerChapter === n && !form.wordsCustom)}>{n}</button>
                  ))}
                  <button onClick={() => setForm({ ...form, wordsCustom: true })}
                    className={SEG.btn(form.wordsCustom)}>custom</button>
                </div>
                {form.wordsCustom && (
                  <input type="number" min={500} max={12000} step={100}
                    className="field-input mt-2 max-w-32" value={form.wordsPerChapter}
                    onChange={(e) => setForm({ ...form, wordsPerChapter: Math.max(500, Math.min(12000, Number(e.target.value) || 0)) })} />
                )}
              </div>
              <div>
                <span className="field-label">revision cycles <Hint>After drafting, the pipeline re-reads the whole novel and adversarially edits it this many times. 3 is the sweet spot.</Hint></span>
                <div className={SEG.wrapper}>
                  {[1, 2, 3, 4].map((n) => (
                    <button key={n} onClick={() => setForm({ ...form, revisionCycles: n })} className={SEG.btn(form.revisionCycles === n)}>{n}</button>
                  ))}
                </div>
              </div>

              {mode === 'creator' && (
                <label className="block">
                  <span className="field-label">
                    optional freeform attach <Hint>Appended after the structured fields. Use for texture, bans, or half-formed ideas — not a replacement for character/arcs.</Hint>
                  </span>
                  <textarea
                    rows={4}
                    className="field-input resize-none"
                    placeholder="no romance subplot; avoid gore; the snow should feel like a character…"
                    value={form.extraNotes}
                    onChange={set('extraNotes')}
                  />
                </label>
              )}
            </>
          )}

          {steps[step] === 'launch' && (
            <div className="space-y-3">
              <div className="space-y-2 border border-line bg-ink-950 p-4 font-mono text-xs leading-relaxed">
                {[
                  ['project folder', form.name],
                  ['working title', form.workingTitle || '— (pipeline will invent one)'],
                  ['genre', form.genre],
                  ['mode', mode === 'creator' ? 'story creator' : 'paste notes'],
                  ['seed notes', mode === 'dump' && form.notesMode === 'path'
                    ? form.notesPath
                    : `${notesWordCount} words`],
                  ['cast', mode === 'creator' ? String(form.cast.filter((c) => c.name.trim()).length + 1) : '—'],
                  ['arcs', mode === 'creator' ? String(form.arcs.length) : '—'],
                  ['reveal', form.revealChapter ? `~ch ${form.revealChapter}` : '—'],
                  ['perspective', form.perspective],
                  ['prose mode', form.proseMode || '—'],
                  ['chapters', `${form.chapters} × ${form.wordsPerChapter}w`],
                  ['revision cycles', String(form.revisionCycles)],
                ].map(([k, v]) => (
                  <p key={k} className="flex justify-between gap-6">
                    <span className="shrink-0 text-fog-500">{k}</span>
                    <span className="truncate text-fog-200">{v}</span>
                  </p>
                ))}
              </div>
              <details className="border border-line">
                <summary className="cursor-pointer px-3 py-2 font-mono text-[10px] text-fog-400 hover:text-fog-200">
                  preview seed notes ({notesWordCount} words)
                </summary>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap border-t border-line p-3 font-mono text-[10px] leading-relaxed text-fog-300">
                  {notesPreview || '(empty)'}
                </pre>
              </details>
              <p className="font-mono text-[10px] leading-relaxed text-fog-500">
                the run starts as a background process. foundation (world, characters, outline) takes about an hour —
                you can close this window and come back any time; the shelf remembers where everything stands.
              </p>
            </div>
          )}

          {error && (
            <p className="border-l-2 border-bad bg-bad/5 px-3 py-2 font-mono text-[11px] leading-relaxed text-bad">{error}</p>
          )}
        </div>

        <footer className="flex items-center justify-between border-t border-line px-6 py-4">
          <Button onClick={() => (step === 0 ? onClose() : setStep(step - 1))} disabled={launching}>
            {step === 0 ? 'cancel' : '‹ back'}
          </Button>
          {step < steps.length - 1 ? (
            <Button variant="accent" disabled={!stepValid} onClick={() => setStep(step + 1)}>next ›</Button>
          ) : (
            <Button variant="solid" disabled={launching || !stepValid} onClick={launch}>
              {launching ? 'launching…' : 'launch run ▸'}
            </Button>
          )}
        </footer>
      </div>
    </div>
  )
}

export default function ProjectsGallery() {
  const { projects, launchProject } = useApp()
  const [wizard, setWizard] = useState(false)

  useEffect(() => {
    document.title = 'gesaku · projects'
  }, [])

  const doLaunch = async (form) => {
    await launchProject({
      name: form.name, genre: form.genre, notes: form.notes, notesPath: form.notesPath,
      chapters: form.chapters, wordsPerChapter: form.wordsPerChapter,
      revisionCycles: form.revisionCycles, perspective: form.perspective,
      proseMode: form.proseMode,
    })
    setWizard(false)
    navigate(projectRoute(form.name))
  }

  return (
    <main className="flex min-w-0 flex-1 overflow-y-auto">
      {/* my-auto centers short content but collapses to 0 when the grid
          outgrows the viewport, so the hero never scrolls out of reach */}
      <div className="mx-auto my-auto w-full max-w-6xl px-6 py-10">
        <header className="mb-10 text-center">
          <p className="section-head">your shelf</p>
          <h1 className="mt-2 font-display text-3xl font-bold lowercase tracking-tight text-paper">projects</h1>
          <p className="mx-auto mt-3 max-w-xl font-prose text-sm leading-relaxed text-fog-400">
            every novel is a project: one folder, one pipeline — foundation, drafting, revision, export.
            open one to watch it write itself, or start a fresh one.
          </p>
          <div className="mt-5">
            <Button variant="accent" onClick={() => setWizard(true)}>+ new project</Button>
          </div>
        </header>

        {!projects ? (
          <div className="dock grid grid-cols-1 gap-px sm:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => <Skel key={i} className="h-52" />)}
          </div>
        ) : projects.length === 0 ? (
          <EmptyState
            icon="✎"
            title="no projects yet"
            cta={<Button variant="accent" onClick={() => setWizard(true)}>+ start your first novel</Button>}
          >
            a project is a novel-in-progress. give the pipeline a genre and a rough premise, and it will build the
            world, outline the plot, draft every chapter, and revise itself — you supervise from the console.
          </EmptyState>
        ) : (
          <div className="dock grid grid-cols-1 gap-px sm:grid-cols-2 xl:grid-cols-3">
            {projects.map((p) => <ProjectCard key={p.name} p={p} />)}
          </div>
        )}
      </div>

      {wizard && <Wizard onClose={() => setWizard(false)} onLaunch={doLaunch} />}
    </main>
  )
}
