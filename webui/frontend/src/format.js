/**
 * Display formatters shared by the project views.
 *
 * These exist because the pipeline's on-disk vocabulary leaked into the UI in
 * two places: chapter keys appeared as both `ch_01` (manuscript rail) and
 * `ch01` (eval logs, score history), and review timestamps rendered as raw
 * `20260814`.
 */

/**
 * Normalize the chapter token to `ch_NN` wherever it appears, so `ch01`,
 * `ch1`, and the `ch12` inside `rev-ch12` all render the same way. Other text
 * (`foundation`, `revision-cycle-6`) is untouched.
 */
export function fmtChapter(ref) {
  return String(ref ?? '').replace(
    /(?<!\w)ch(\d{1,3})\b/g,
    (_, n) => `ch_${String(n).padStart(2, '0')}`,
  )
}

/** `20260814`, `20260814_120000`, or an ISO string -> `2026-08-14`. */
export function fmtStamp(ts) {
  const s = String(ts ?? '')
  const m = /^(\d{4})(\d{2})(\d{2})/.exec(s)
  if (m) return `${m[1]}-${m[2]}-${m[3]}`
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? s : d.toISOString().slice(0, 10)
}
