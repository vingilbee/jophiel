import { imageUrl } from '../api/images'
import type { AwarenessSnapshot } from '../types'

/**
 * What the assistant is currently aware of, as three sense readouts.
 *
 * This is a trust surface, not a debug panel. An assistant that quietly knows
 * the time, hears a microphone, and (later) watches a screen has to be legible
 * about it — the strip answers "what can it perceive right now?" at a glance,
 * and the tooltip shows the exact narration the model receives, verbatim.
 *
 * Each sense reads open / closed rather than on / off: the agent has senses,
 * not settings.
 */

interface Props {
  awareness: AwarenessSnapshot | null
  /** Turn screen sharing on or off. Omit to render the eye as read-only. */
  onToggleEye?: () => void
  /** True between the click and the first frame arriving. */
  eyeOpening?: boolean
  eyeError?: string | null
  className?: string
}

type Tone = 'live' | 'armed' | 'closed'

const TONE: Record<Tone, string> = {
  live: 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]',
  armed: 'border-[#DCCFF2] bg-[#F2EFFB] text-[#765E94]',
  closed: 'border-[#D9E8F6] bg-[#F8FAFF] text-[#7183A3]',
}

export function SenseStrip({
  awareness,
  onToggleEye,
  eyeOpening = false,
  eyeError = null,
  className = '',
}: Props) {
  if (!awareness) {
    return (
      <div className={`rounded-2xl border border-[#D9E8F6] bg-white/90 p-4 shadow-[0_8px_24px_rgba(35,49,79,0.04)] ${className}`}>
        <Heading />
        <p className="mt-3 text-sm text-[#7183A3]">Waking up…</p>
      </div>
    )
  }

  const { senses } = awareness
  const time = new Date(awareness.now)

  const hearing: Tone = !senses.hearing_enabled
    ? 'closed'
    : senses.voice_state === 'listening'
      ? 'live'
      : 'armed'

  return (
    <div
      className={`rounded-2xl border border-[#D9E8F6] bg-white/90 p-4 shadow-[0_8px_24px_rgba(35,49,79,0.04)] backdrop-blur-sm ${className}`}
      title={awareness.narration}
    >
      <Heading />

      <div className="mt-3 space-y-2">
        <Sense
          label="Eyes"
          tone={senses.vision_enabled ? 'live' : 'closed'}
          value={
            !senses.vision_enabled
              ? 'Closed'
              : eyeOpening || !senses.screen_image_id
                ? 'Opening…'
                : 'Watching screen'
          }
          glyph={<EyeGlyph closed={!senses.vision_enabled} />}
          onClick={onToggleEye}
          actionLabel={
            senses.vision_enabled ? 'Stop sharing your screen' : 'Share your screen'
          }
        />
        <Sense
          label="Ears"
          tone={hearing}
          value={
            !senses.hearing_enabled
              ? 'Closed'
              : senses.voice_state === 'listening'
                ? 'Listening'
                : senses.wake_armed
                  ? 'Waiting for “columbina”'
                  : 'Mic live'
          }
          glyph={<EarGlyph closed={!senses.hearing_enabled} />}
        />
        <Sense
          label="Time"
          tone="live"
          value={`${time.toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
          })} · ${awareness.part_of_day}`}
          glyph={<ClockGlyph />}
        />
      </div>

      {/* What the eye actually sees. Showing the frame *and* the sentence
          written about it is the point: it's the only way to check that what
          the assistant is told matches what's on your screen. */}
      {senses.vision_enabled && senses.screen_image_id && (
        <figure className="mt-3 border-t border-[#EAF0FA] pt-3">
          <img
            src={imageUrl(senses.screen_image_id)}
            alt="The most recent frame of your shared screen"
            className="w-full rounded-xl border border-[#D9E8F6] object-cover"
          />
          <figcaption className="mt-2 text-xs leading-relaxed text-[#7183A3]">
            {awareness.screen_caption ?? 'Not described yet — nothing has asked.'}
          </figcaption>
        </figure>
      )}

      {eyeError && (
        <p className="mt-3 rounded-xl border border-[#E7B5C5] bg-[#FBEFF3] px-2 py-1.5 text-xs text-[#8B3158]">
          {eyeError}
        </p>
      )}

      <p className="mt-3 border-t border-[#EAF0FA] pt-3 text-xs leading-relaxed text-[#7183A3]">
        {awareness.entries_today > 0
          ? `${awareness.calories_today.toLocaleString()} kcal logged today across ${
              awareness.entries_today
            } ${awareness.entries_today === 1 ? 'entry' : 'entries'}.`
          : 'Nothing logged today yet.'}
      </p>
    </div>
  )
}

function Heading() {
  return (
    <h2 className="text-[11px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase">
      Awareness
    </h2>
  )
}

function Sense({
  label,
  value,
  tone,
  glyph,
  onClick,
  actionLabel,
}: {
  label: string
  value: string
  tone: Tone
  glyph: React.ReactNode
  /** When given, the whole row becomes the control for this sense. */
  onClick?: () => void
  actionLabel?: string
}) {
  const body = (
    <>
      <span className="flex items-center gap-2 text-xs text-[#7183A3]">
        <span className={tone === 'closed' ? 'text-[#B0BDD0]' : 'text-[#6FAED3]'}>
          {glyph}
        </span>
        {label}
      </span>
      <span
        className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${TONE[tone]}`}
      >
        {value}
      </span>
    </>
  )

  if (!onClick) {
    return <div className="flex items-center justify-between gap-3">{body}</div>
  }
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={actionLabel}
      className="-mx-1 flex w-[calc(100%+0.5rem)] cursor-pointer items-center justify-between gap-3 rounded-md px-1 py-0.5 transition-colors outline-none hover:bg-[#F4F8FD] focus-visible:ring-2 focus-visible:ring-[#8FC7E8]"
    >
      {body}
    </button>
  )
}

const ICON = 'h-3.5 w-3.5'

function EyeGlyph({ closed }: { closed: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={ICON}>
      {closed ? (
        // A closed lid, not a struck-through eye: the sense is resting, not broken.
        <path
          d="M3 12c2.5 3.5 5.6 5.2 9 5.2s6.5-1.7 9-5.2M5 15.4 3.6 17.4M12 17.2V19.6M19 15.4l1.4 2"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      ) : (
        <>
          <path
            d="M2.5 12S6 6.2 12 6.2 21.5 12 21.5 12 18 17.8 12 17.8 2.5 12 2.5 12Z"
            stroke="currentColor"
            strokeWidth="1.6"
          />
          <circle cx="12" cy="12" r="2.6" stroke="currentColor" strokeWidth="1.6" />
        </>
      )}
    </svg>
  )
}

function EarGlyph({ closed }: { closed: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={ICON}>
      <path
        d="M12 4a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V7a3 3 0 0 1 3-3ZM5.5 11.5a6.5 6.5 0 0 0 13 0M12 18.5V21"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      {closed && (
        <path d="M4 20 20 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      )}
    </svg>
  )
}

function ClockGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={ICON}>
      <circle cx="12" cy="12" r="8.2" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M12 7.6V12l2.8 1.8"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  )
}
