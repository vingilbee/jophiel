import { useEffect, useRef } from 'react'
import { AppHeader } from '../components/AppHeader'
import { Button } from '../components/Button'
import { SenseStrip } from '../components/SenseStrip'
import { VoiceOrb } from '../components/VoiceOrb'
import { heardCut } from '../audio/heardText'
import { ColumbinaFeathers } from '../components/ColumbinaFeathers'
import { useAwareness } from '../hooks/useAwareness'
import { useScreenSense } from '../hooks/useScreenSense'
import { useVoiceAgent, type VoiceMessage } from '../hooks/useVoiceAgent'
import type { VoiceState } from '../hooks/useVoiceSession'
import voiceWallpaper from '../assets/wallpaper3.jpg'

/**
 * The voice page: one hands-free conversation loop, front and centre.
 *
 * Voice used to live as a line of text in the chat header, which made the
 * assistant's state (armed? listening? thinking?) something you had to read
 * rather than see. Here the whole page is the state — orb motion, colour,
 * headline, and which action the buttons offer all change together, so the
 * answer to "is it hearing me?" is available from across the room.
 *
 * The transcript is secondary and deliberately compact: it's a record of the
 * spoken turns, not a chat composer. Typing still lives on /chat, and both share
 * one conversation thread server-side.
 */

interface Copy {
  title: string
  detail: string
  /** The action the orb (and the primary button) performs in this state. */
  action: string
}

function copyFor(state: VoiceState, wakeWordAvailable: boolean): Copy {
  switch (state) {
    case 'off':
      return {
        title: 'Voice is off',
        detail: 'Start a session and the mic stays open for a hands-free conversation.',
        action: 'Start voice',
      }
    case 'idle':
      return {
        title: wakeWordAvailable ? 'Waiting for “columbina”' : 'Ready when you are',
        detail: wakeWordAvailable
          ? 'Say the wake word, or tap to start talking right away.'
          : 'Tap the orb or press Space, then just talk.',
        action: 'Talk now',
      }
    case 'listening':
      return {
        title: 'Listening',
        detail: 'Speak naturally — I stop recording when you stop talking.',
        action: "I'm done",
      }
    case 'thinking':
      return {
        title: 'Thinking',
        detail: 'Working out the answer.',
        action: 'Interrupt',
      }
    case 'speaking':
      return {
        title: 'Speaking',
        detail: 'Talk over me any time to cut in.',
        action: 'Interrupt',
      }
  }
}

/** Short, colour-coded state chip — the same vocabulary as the orb. */
const CHIP: Record<VoiceState, string> = {
  off: 'border-[#D9E8F6] bg-white text-[#7183A3]',
  idle: 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]',
  listening: 'border-[#8FC7E8] bg-[#EAF6FF] text-[#527998]',
  thinking: 'border-[#DCCFF2] bg-[#F2EFFB] text-[#765E94]',
  speaking: 'border-[#E7B5C5] bg-[#FBEFF3] text-[#8B3158]',
}

export function DashboardPage() {
  const transcriptRef = useRef<HTMLDivElement>(null)
  const {
    voice,
    status,
    awareness,
    sendSense,
    dismissNudges,
    messages,
    clearConversation,
    voiceEnabled,
  } = useVoiceAgent()

  const { state, wakeWordAvailable, error } = voice
  const copy = voiceEnabled
    ? copyFor(state, wakeWordAvailable)
    : {
        title: 'Voice unavailable',
        detail: 'Voice is disabled by the server configuration.',
        action: 'Voice unavailable',
      }
  const live = state !== 'off'

  const eye = useScreenSense()

  // Tell the server what this client can sense. Ids and flags only — the
  // pixels behind `eye.imageId` were uploaded separately, and the audio never
  // travels this path at all.
  useAwareness({
    sendSense,
    activity: state === 'listening' ? 'speaking' : 'idle',
    hearingEnabled: live,
    wakeArmed: live && wakeWordAvailable && state === 'idle',
    voiceState: state,
    visionEnabled: eye.status !== 'closed',
    screenImageId: eye.imageId,
  })

  // Settles towards 1 over ten minutes of quiet, which the orb breathes with.
  const quietFor = awareness?.since_last_message_seconds ?? 0
  const calm = Math.min(1, quietFor / 600)

  /** What the orb and the primary button do, given the current state. */
  function primaryAction() {
    if (!voiceEnabled) return
    if (state === 'off') void voice.enable()
    else if (state === 'idle') voice.pushToTalk()
    else if (state === 'listening') voice.endTurn()
    else voice.interrupt()
  }

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight })
  }, [messages])

  // Space is the natural push-to-talk key and this page has no text input to
  // steal it — but still bail out if focus is on a control, or Space would both
  // press the focused button and fire the shortcut. The handler is bound once
  // and reads the current action from a ref.
  const keyActionsRef = useRef({ primaryAction, disable: voice.disable, live })
  keyActionsRef.current = { primaryAction, disable: voice.disable, live }
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null
      if (target && target !== document.body) return
      if (e.code === 'Space') {
        e.preventDefault()
        keyActionsRef.current.primaryAction()
      } else if (e.key === 'Escape' && keyActionsRef.current.live) {
        keyActionsRef.current.disable()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return (
    <div className="flex min-h-screen flex-col bg-[#F8FAFF] text-[#23314F] md:flex-row">
      <AppHeader>
        <span
          className="flex items-center gap-1.5 font-mono text-[11px] tracking-wider text-muted uppercase"
          title={`WebSocket: ${status}`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              status === 'open'
                ? 'bg-accent'
                : status === 'connecting'
                  ? 'animate-pulse-dot bg-red-400'
                  : 'bg-red-400'
            }`}
          />
          {status === 'open' ? 'Live' : status === 'connecting' ? 'Connecting' : 'Offline'}
        </span>
      </AppHeader>

      <main className="relative flex-1 overflow-hidden bg-[#F8FAFF] px-4 py-8 sm:px-6 lg:px-10">
        <img
          src={voiceWallpaper}
          alt=""
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 h-full w-full object-cover object-center opacity-[0.12]"
        />
        <ColumbinaFeathers className="pointer-events-none absolute top-8 left-1/2 w-80 -translate-x-1/2 opacity-50" />
        <div className="relative z-10 mx-auto grid max-w-6xl gap-10 lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start lg:gap-14">
          {/* voice stage */}
          <section className="relative flex flex-col items-center">
            <span
              className={`rounded-full border px-3 py-1 font-mono text-[10px] tracking-[0.18em] uppercase transition-colors ${CHIP[state]}`}
            >
              {state === 'off' ? 'Standby' : state}
            </span>

            <button
              type="button"
              onClick={primaryAction}
              aria-label={copy.action}
              disabled={!voiceEnabled}
              className="mt-6 w-full max-w-[16rem] cursor-pointer rounded-full transition-transform outline-none focus-visible:ring-2 focus-visible:ring-accent/60 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-70 sm:max-w-[20rem] lg:max-w-[24rem]"
            >
              <div className="rounded-full bg-white/60 p-3 shadow-[0_15px_45px_rgba(35,49,79,0.08)]">
                <VoiceOrb state={state} getLevel={voice.getLevel} calm={calm} />
              </div>
            </button>

            {/* Announced, because the orb's motion carries this same meaning. */}
            <div className="mt-2 text-center" aria-live="polite">
              <h1 className="text-2xl font-semibold tracking-[-0.03em] sm:text-3xl">{copy.title}</h1>
              <p className="mx-auto mt-2 max-w-sm text-sm text-[#7183A3]">{copy.detail}</p>
            </div>

            <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
              <Button
                onClick={primaryAction}
                variant={state === 'off' || state === 'idle' ? 'primary' : 'outline'}
                disabled={!voiceEnabled || (status !== 'open' && state === 'off')}
              >
                {copy.action}
              </Button>
              {live && (
                <Button variant="ghost" onClick={voice.disable}>
                  Turn off
                </Button>
              )}
            </div>

            {error && (
              <p className="mt-5 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {error}
              </p>
            )}
            {status !== 'open' && !error && (
              <p className="mt-5 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {status === 'connecting'
                  ? 'Connecting to the assistant…'
                  : 'Disconnected — retrying. Voice will work once the connection is back.'}
              </p>
            )}

            <p className="mt-6 hidden font-mono text-[10px] tracking-wider text-muted/60 uppercase sm:block">
              Space to talk · Esc to turn voice off
            </p>
          </section>

          {/* awareness + transcript */}
          <div className="flex flex-col gap-4">
          <SenseStrip
            awareness={awareness}
            onToggleEye={eye.toggle}
            eyeOpening={eye.status === 'opening'}
            eyeError={eye.error}
          />

          <section className="flex flex-col rounded-2xl border border-[#D9E8F6] bg-white/90 p-4 shadow-[0_8px_24px_rgba(35,49,79,0.04)] backdrop-blur-sm">
            <div className="flex items-center justify-between">
              <h2 className="text-[11px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase">
                Transcript
              </h2>
              {messages.length > 0 && (
                <button
                  onClick={() => {
                    clearConversation()
                  }}
                  disabled={live && state !== 'idle'}
                  className="text-[11px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase transition-colors hover:text-[#8B3158] disabled:opacity-40"
                >
                  New
                </button>
              )}
            </div>

            <div
              ref={transcriptRef}
              className="mt-3 max-h-72 space-y-3 overflow-y-auto lg:max-h-[26rem]"
            >
              {messages.length === 0 ? (
                <p className="py-8 text-center text-sm text-[#7183A3]">
                  Nothing said yet. Your spoken turns show up here as they're
                  transcribed.
                </p>
              ) : (
                messages.map((m, i) => (
                  <TranscriptTurn
                    key={i}
                    message={m}
                    onDismissNudge={dismissNudges}
                  />
                ))
              )}
            </div>
          </section>
          </div>
        </div>
      </main>
    </div>
  )
}

function TranscriptTurn({
  message,
  onDismissNudge,
}: {
  message: VoiceMessage
  onDismissNudge?: () => void
}) {
  const isUser = message.role === 'user'
  return (
    <div className={isUser ? 'text-right' : 'text-left'}>
      <span className={`text-[10px] font-semibold tracking-[0.08em] uppercase ${isUser ? 'text-[#7183A3]' : 'text-[#8B3158]'}`}>
        {isUser ? 'You' : 'Columbina'}
      </span>
      {/* Label it. An unprompted message the user can't distinguish from a
          reply reads as the app talking to itself. */}
      {message.unprompted && (
        <>
          <span className="ml-2 text-[10px] font-semibold tracking-[0.08em] text-[#8B3158] uppercase">
            Unprompted
          </span>
          {onDismissNudge && (
            <button
              onClick={onDismissNudge}
              className="ml-2 text-[10px] tracking-[0.08em] text-[#7183A3] uppercase transition-colors hover:text-[#23314F]"
              title="Stop the assistant from speaking first this session"
            >
              Not now
            </button>
          )}
        </>
      )}
      <div
        className={`mt-1 inline-block max-w-full rounded-xl px-3 py-2 text-left text-sm leading-relaxed whitespace-pre-wrap ${
          isUser ? 'bg-[#EAF6FF] text-[#23314F]' : 'border border-[#D9E8F6] bg-white text-[#23314F]'
        }`}
      >
        {message.content ? <MessageText message={message} /> : (
          <span className="inline-flex gap-1 py-1 align-middle">
            <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-[#8B3158]" />
            <span
              className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-[#8B3158]"
              style={{ animationDelay: '0.2s' }}
            />
            <span
              className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-[#8B3158]"
              style={{ animationDelay: '0.4s' }}
            />
          </span>
        )}
      </div>
      {(message.stickers ?? []).length > 0 && (
        <div className={`mt-1 flex flex-wrap gap-1 ${isUser ? 'justify-end' : 'justify-start'}`}>
          {message.stickers?.map((sticker, index) => (
            <img
              key={`${sticker}-${index}`}
              src={`/api/chat/stickers/${encodeURIComponent(sticker)}.png`}
              alt={`Columbina sticker: ${sticker}`}
              className="h-28 w-28 object-contain"
            />
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * A reply the user cut off shows the part they never heard faded: it's what
 * she was going to say, but she knows it wasn't said.
 */
function MessageText({ message }: { message: VoiceMessage }) {
  const cut = heardCut(message.content, message.heardChars)
  if (cut >= message.content.length) return <span>{message.content}</span>
  return (
    <>
      <span>{message.content.slice(0, cut)}</span>
      <span className="text-[#23314F]/35" title="Not heard: you interrupted here">
        {message.content.slice(cut)}
      </span>
    </>
  )
}
