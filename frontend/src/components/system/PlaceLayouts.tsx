import { useCallback, useEffect, useState } from 'react'
import { placesApi } from '../../api/practice'
import type { PlaceEntry, PlaceEntryKind, PlaceLayout } from '../../types/practice'

type Act = (fn: () => Promise<unknown>) => Promise<void>

const KIND_LABEL: Record<PlaceEntryKind, string> = {
  control: 'Control',
  landmark: 'Landmark',
  area: 'Area',
  thing: 'Thing',
}

/** What she knows of each place that stays the same next time (where an app's controls sit, her own village, farm
 *  or world and what she saw where): kept from her screenshots one thing at a time, per world, with her own lines
 *  beside it. Fix or remove one thing, or forget a place. */
export function PlaceLayouts() {
  const [places, setPlaces] = useState<PlaceLayout[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setPlaces(await placesApi.list())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load what she knows of places.')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const act: Act = async (fn) => {
    try {
      await fn()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.')
    }
  }

  return (
    <section className="mb-10">
      <h2 className="text-sm font-semibold">What she knows of each place</h2>
      <p className="mt-1 mb-4 text-xs text-[#8C97A3]">
        While her hands use your phone, she keeps track of what she's doing and what's where from her screenshots.
        What she's doing right now is gone an hour after she stops (or when Phone control goes off); what holds next
        time is listed here, per world when the game names one.
      </p>
      {error && <p className="mb-3 text-[13px] text-[#FF8A80]">{error}</p>}
      {places === null && !error && <p className="text-[13px] text-[#8C97A3]">Reading…</p>}
      {places !== null && places.length === 0 && (
        <p className="text-[13px] text-[#8C97A3]">Nothing yet. It comes as she uses an app or game on your phone.</p>
      )}
      <ul className="flex flex-col gap-3">
        {(places ?? []).map((place) => (
          <Place key={place.id} place={place} act={act} />
        ))}
      </ul>
    </section>
  )
}

/** Entries of one world in reading order: controls, then each landmark or area with the things near it, then
 *  the rest. */
function ordered(entries: PlaceEntry[]): { entry: PlaceEntry; nested: boolean }[] {
  const ids = new Set(entries.map((e) => e.id))
  const holders = new Set(
    entries.filter((e) => e.kind === 'landmark' || e.kind === 'area').map((e) => e.id),
  )
  const near = (e: PlaceEntry) => e.kind === 'thing' && e.near_id !== null && ids.has(e.near_id) && holders.has(e.near_id)
  const out: { entry: PlaceEntry; nested: boolean }[] = []
  for (const e of entries) if (e.kind === 'control') out.push({ entry: e, nested: false })
  for (const e of entries) {
    if (e.kind === 'control' || near(e)) continue
    out.push({ entry: e, nested: false })
    for (const t of entries) if (near(t) && t.near_id === e.id) out.push({ entry: t, nested: true })
  }
  return out
}

function Place({ place, act }: { place: PlaceLayout; act: Act }) {
  const [editing, setEditing] = useState(false)
  const [hers, setHers] = useState(place.her_layout)
  const worlds = [...new Set(place.entries.map((e) => e.world))].sort()
  return (
    <li className="rounded-md border border-[#1C2228] bg-[#0B0E11] px-3 py-2">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <h3 className="text-[13px] font-semibold text-[#C9D1D9]">{place.place}</h3>
        <button
          type="button"
          className="text-[12px] text-[#8C97A3] hover:text-[#F08AB0]"
          onClick={() => {
            if (window.confirm(`Forget everything she knows of ${place.place}?`))
              void act(() => placesApi.remove(place.id))
          }}
        >
          Forget place
        </button>
      </div>
      {worlds.map((world) => (
        <div key={world} className="mt-2">
          {(worlds.length > 1 || world) && (
            <h4 className="mb-1 text-[12px] text-[#8C97A3]">{world ? `World ${world}` : 'No world named'}</h4>
          )}
          <ul className="flex flex-col gap-1">
            {ordered(place.entries.filter((e) => e.world === world)).map(({ entry, nested }) => (
              <Entry key={entry.id} entry={entry} nested={nested} act={act} />
            ))}
          </ul>
        </div>
      ))}
      <div className="mt-3 border-t border-[#1C2228] pt-2">
        {editing ? (
          <div className="flex flex-col gap-2">
            <textarea
              value={hers}
              onChange={(event) => setHers(event.target.value)}
              aria-label="Her own lines"
              rows={2}
              className="rounded border border-[#1C2228] bg-[#0F1317] px-2 py-1 text-[13px]"
            />
            <div className="flex gap-3 text-[12px] text-[#8C97A3]">
              <button
                type="button"
                className="text-[#7FB8D8]"
                onClick={() =>
                  void act(() => placesApi.update(place.id, { her_layout: hers })).then(() => setEditing(false))
                }
              >
                Save
              </button>
              <button
                type="button"
                onClick={() => {
                  setHers(place.her_layout)
                  setEditing(false)
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <p className="text-[13px] leading-relaxed text-[#B9A2F0]">
            {place.her_layout ? `Her own lines: ${place.her_layout}` : 'No lines of her own.'}{' '}
            <button type="button" className="text-[12px] text-[#8C97A3] hover:text-[#C9D1D9]" onClick={() => setEditing(true)}>
              Edit
            </button>
          </p>
        )}
      </div>
    </li>
  )
}

function Entry({ entry, nested, act }: { entry: PlaceEntry; nested: boolean; act: Act }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(entry.text)
  return (
    <li className={`text-[13px] leading-relaxed ${nested ? 'ml-5' : ''}`}>
      <span className="mr-2 text-[11px] uppercase tracking-wide text-[#8C97A3]">{KIND_LABEL[entry.kind]}</span>
      {editing ? (
        <span className="flex flex-col gap-1">
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            aria-label={`${KIND_LABEL[entry.kind]} entry`}
            rows={2}
            maxLength={240}
            className="rounded border border-[#1C2228] bg-[#0F1317] px-2 py-1 text-[13px]"
          />
          <span className="flex gap-3 text-[12px] text-[#8C97A3]">
            <button
              type="button"
              className="text-[#7FB8D8]"
              disabled={!text.trim()}
              onClick={() => void act(() => placesApi.updateEntry(entry.id, text)).then(() => setEditing(false))}
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => {
                setText(entry.text)
                setEditing(false)
              }}
            >
              Cancel
            </button>
          </span>
        </span>
      ) : (
        <>
          {entry.text}{' '}
          <span className="ml-1 inline-flex gap-2 text-[12px] text-[#8C97A3]">
            <button type="button" className="hover:text-[#C9D1D9]" onClick={() => setEditing(true)}>
              Edit
            </button>
            <button
              type="button"
              className="hover:text-[#F08AB0]"
              onClick={() => void act(() => placesApi.removeEntry(entry.id))}
            >
              Remove
            </button>
          </span>
        </>
      )}
    </li>
  )
}
