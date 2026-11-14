// How the System page says times and record words: "4 min" ago, a clock time, snake_case as words.

export function ago(at: string | number | null | undefined): string {
  if (at === null || at === undefined || at === '') return ''
  const ms = typeof at === 'number' ? at * 1000 : new Date(at).getTime()
  const s = Math.max(0, (Date.now() - ms) / 1000)
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.round(s / 60)} min`
  if (s < 86400) return `${Math.round(s / 3600)} h`
  return `${Math.round(s / 86400)} d`
}

/** A duration counted in seconds, said the way a person would: "4 min", "1 h 12 min". */
export function span(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} s`
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return m ? `${h} h ${m} min` : `${h} h`
}

/** The local clock time of a moment, for the full time beside the "ago". */
export function clock(at: string | number | null | undefined): string {
  if (at === null || at === undefined || at === '') return ''
  const d = new Date(typeof at === 'number' ? at * 1000 : at)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export const join = (...bits: (string | null | undefined | false | 0)[]) => bits.filter(Boolean).join(' · ')

export const words = (s: string | null | undefined) => (s ?? '').replaceAll('_', ' ')

/** The anchor named by an address's fragment ("#lessons" -> "lessons"), or null when there is none or it doesn't decode. */
export function anchorFromHash(hash: string): string | null {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash
  if (!raw) return null
  try {
    return decodeURIComponent(raw) || null
  } catch {
    return null // a malformed fragment such as "#%" names nothing
  }
}
