import { useCallback, useEffect, useMemo, useState } from 'react'
import { practiceApi } from '../../api/practice'
import type { PracticeNote } from '../../types/practice'

const BADGE: Record<string, string> = {
  known: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  to_try: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  yours: 'border-[#3A2E58] bg-[#1A1528] text-[#B9A2F0]',
}

/** What she learned using her tools, per app: hers from practice, yours by your word. Edit or remove any;
 *  an edit makes a note yours, so her reviews leave it alone. Pin one to keep it as it is without making it yours. */
export function PracticeNotes() {
  const [notes, setNotes] = useState<PracticeNote[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [place, setPlace] = useState('')
  const [text, setText] = useState('')

  const load = useCallback(async () => {
    try {
      setNotes(await practiceApi.notes())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load her notes.')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.')
    }
  }

  const byPlace = useMemo(() => {
    const groups = new Map<string, PracticeNote[]>()
    for (const note of notes ?? []) {
      const list = groups.get(note.place) ?? []
      list.push(note)
      groups.set(note.place, list)
    }
    return [...groups.entries()]
  }, [notes])

  return (
    <section className="mt-12 mb-10">
      <h2 className="text-sm font-semibold">Her notes from using her tools</h2>
      <p className="mt-1 mb-4 text-xs text-[#8C97A3]">
        After her hands use your phone, she looks back at what she did and keeps notes per app. She reads them with the
        screen whenever that app is in front. Yours are never changed by her reviews.
      </p>
      {error && <p className="mb-3 text-[13px] text-[#FF8A80]">{error}</p>}
      <form
        className="mb-6 flex flex-col gap-2 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault()
          if (!place.trim() || !text.trim()) return
          void act(async () => {
            await practiceApi.create(place.trim(), text.trim())
            setText('')
          })
        }}
      >
        <input
          value={place}
          onChange={(event) => setPlace(event.target.value)}
          placeholder="App (as the screen names it)"
          aria-label="App"
          className="rounded-md border border-[#1C2228] bg-[#0F1317] px-3 py-2 text-[13px] sm:w-56"
        />
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="A note of yours, e.g. drop the Warden last"
          aria-label="Note"
          className="min-w-0 flex-1 rounded-md border border-[#1C2228] bg-[#0F1317] px-3 py-2 text-[13px]"
        />
        <button type="submit" className="rounded-md border border-[#2B4A5C] px-3 py-2 text-[13px] text-[#7FB8D8]">
          Add
        </button>
      </form>
      {notes === null && !error && <p className="text-[13px] text-[#8C97A3]">Reading…</p>}
      {notes !== null && notes.length === 0 && (
        <p className="text-[13px] text-[#8C97A3]">No notes yet. They come after she uses your phone for something.</p>
      )}
      {byPlace.map(([where, list]) => (
        <div key={where} className="mb-6">
          <h3 className="mb-2 text-[13px] font-semibold text-[#C9D1D9]">{where}</h3>
          <ul className="flex flex-col gap-2">
            {list.map((note) => (
              <Note key={note.id} note={note} act={act} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}

function Note({ note, act }: { note: PracticeNote; act: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(note.text)
  const label = note.source === 'user' ? 'yours' : note.status === 'to_try' ? 'to try' : 'known'
  const badge = BADGE[note.source === 'user' ? 'yours' : note.status]
  return (
    <li className="rounded-md border border-[#1C2228] bg-[#0B0E11] px-3 py-2">
      <div className="flex items-start gap-2">
        <span className={`mt-0.5 shrink-0 rounded border px-1.5 text-[11px] ${badge}`}>{label}</span>
        {note.pinned && (
          <span
            className="mt-0.5 shrink-0 rounded border border-[#5C4A2B] bg-[#221A0E] px-1.5 text-[11px] text-[#D8B87F]"
            title="Her reviews read it but never rewrite or remove it"
          >
            pinned
          </span>
        )}
        {editing ? (
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            aria-label="Edit note"
            rows={2}
            className="min-w-0 flex-1 rounded border border-[#1C2228] bg-[#0F1317] px-2 py-1 text-[13px]"
          />
        ) : (
          <p className="min-w-0 flex-1 text-[13px] leading-relaxed">{note.text}</p>
        )}
      </div>
      {note.evidence && !editing && <p className="mt-1 pl-12 text-[12px] text-[#8C97A3]">From: {note.evidence}</p>}
      <div className="mt-1 flex gap-3 pl-12 text-[12px] text-[#8C97A3]">
        {note.tool && <span>{note.tool}</span>}
        {editing ? (
          <>
            <button
              type="button"
              className="text-[#7FB8D8]"
              onClick={() => void act(() => practiceApi.update(note.id, { text: draft })).then(() => setEditing(false))}
            >
              Save
            </button>
            <button type="button" onClick={() => { setDraft(note.text); setEditing(false) }}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button type="button" className="hover:text-[#C9D1D9]" onClick={() => setEditing(true)}>
              Edit
            </button>
            <button
              type="button"
              className="hover:text-[#D8B87F]"
              aria-pressed={note.pinned}
              onClick={() => void act(() => practiceApi.update(note.id, { pinned: !note.pinned }))}
            >
              {note.pinned ? 'Unpin' : 'Pin'}
            </button>
            <button
              type="button"
              className="hover:text-[#F08AB0]"
              onClick={() => {
                if (window.confirm('Remove this note?')) void act(() => practiceApi.remove(note.id))
              }}
            >
              Remove
            </button>
          </>
        )}
      </div>
    </li>
  )
}
