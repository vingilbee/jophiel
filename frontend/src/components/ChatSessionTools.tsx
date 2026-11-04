import { useEffect, useState } from 'react'
import { DesktopSessionPanel } from './DesktopSessionPanel'
import { MindPanel } from './MindPanel'
import { SenseStrip } from './SenseStrip'
import { useDesktopSession } from '../hooks/useDesktopSession'
import { useAuth } from '../hooks/useAuth'
import { useInnerState } from '../hooks/useInnerState'
import type { ScreenSenseState } from '../hooks/useScreenSense'
import type { VoiceSession } from '../hooks/useVoiceSession'
import type { AwarenessSnapshot, Thought } from '../types'

const CHIP = 'rounded-full border border-[#D9E8F6] bg-white px-3 py-2 text-[11px] text-[#617698] transition-colors hover:bg-[#EAF6FF] focus-visible:outline-2 focus-visible:outline-[#8FC7E8] disabled:opacity-40'
const TAB = 'rounded-full px-3 py-1.5 text-[11px] transition-colors focus-visible:outline-2 focus-visible:outline-[#8FC7E8]'
type Panel = 'mind' | 'game' | 'senses'
const SESSION_LABEL = {
  starting: 'Starting', running: 'Playing', paused: 'Paused', manual: 'You have control',
  disconnected: 'Disconnected', ended: 'Ended', failed: 'Failed',
}

export function ChatSessionTools({ eye, voice, awareness, thoughts, onPanelChange, inputBlocked }: {
  eye: ScreenSenseState
  voice: VoiceSession
  awareness: AwarenessSnapshot | null
  thoughts: Thought[]
  onPanelChange: (open: boolean) => void
  inputBlocked: boolean
}) {
  const { user } = useAuth()
  const owner = Boolean(user?.is_owner)
  const desktop = useDesktopSession()
  const inner = useInnerState(owner)
  const [panel, setPanel] = useState<Panel | null>(null)
  const live = desktop.session && !['ended', 'failed'].includes(desktop.session.state)
  const ears = voice.state === 'off' ? 'Closed' : voice.muted ? 'Muted' : voice.state === 'listening' ? 'Listening' : 'Mic on'
  const sessionId = live ? desktop.session?.id : null
  const eyes = eye.status === 'open' ? 'sharing screen' : eye.status === 'opening' ? 'opening…' : 'screen off'
  const mic = voice.state === 'off' ? 'mic off' : voice.muted ? 'mic muted' : voice.state === 'listening' ? 'listening' : 'mic on'
  const toggle = (next: Panel) => setPanel(panel === next ? null : next)
  const tabs: { id: Panel; label: string }[] = [
    ...(owner ? [{ id: 'mind' as const, label: 'Mind' }] : []),
    ...(desktop.enabled ? [{ id: 'game' as const, label: 'Game' }] : []),
    { id: 'senses', label: 'Senses' },
  ]

  useEffect(() => { if (sessionId) setPanel('game') }, [sessionId])
  useEffect(() => { onPanelChange(panel !== null) }, [panel, onPanelChange])
  useEffect(() => {
    if (!panel) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setPanel(null) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [panel])

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2" aria-label="Mind, game and senses">
        {owner && <button type="button" className={CHIP} onClick={() => toggle('mind')} aria-expanded={panel === 'mind'} aria-controls="chat-session-panel">
          Mind{inner ? `: ${inner.words.mood}` : ''}
        </button>}
        {desktop.enabled && <button type="button" className={CHIP} onClick={() => toggle('game')} aria-expanded={panel === 'game'} aria-controls="chat-session-panel">{live && desktop.session ? `Game: ${SESSION_LABEL[desktop.session.state]}` : 'Game session'}</button>}
        <button type="button" className={CHIP} onClick={() => toggle('senses')} aria-expanded={panel === 'senses'} aria-controls="chat-session-panel">
          Senses: {eyes} · {mic}
        </button>
        {live && desktop.session && <div className="flex flex-wrap items-center gap-2 text-[11px] text-[#527998]">
          <span role="status">{desktop.releasePending ? 'Releasing controls…' : desktop.environment?.hosting_kind === 'game_pack' ? 'Jophiel’s own player' : desktop.session.control_owner === 'gameplay' ? 'Jophiel has control' : desktop.session.control_owner === 'manual' ? 'You have control' : 'Controls released'}</span>
          {desktop.session.state === 'running' && <button type="button" className={CHIP} disabled={desktop.busy} onClick={() => void desktop.pause()}>Pause</button>}
          {desktop.session.control_owner === 'gameplay' && <button type="button" className={CHIP} disabled={desktop.busy} onClick={() => void desktop.takeover()}>Take over</button>}
          <button type="button" className={CHIP} disabled={desktop.busy} onClick={() => void desktop.end()}>End game</button>
        </div>}
      </div>
      {desktop.error && <p role="alert" className="mb-2 text-xs text-[#8B3158]">{desktop.error}</p>}
      {panel && <>
        <button type="button" className="fixed inset-0 z-30 bg-[#23314F]/20 lg:hidden" aria-label="Close session panel" onClick={() => setPanel(null)} />
        <aside id="chat-session-panel" aria-label={tabs.find((t) => t.id === panel)?.label} className="fixed inset-x-0 bottom-0 z-40 max-h-[80dvh] overflow-y-auto rounded-t-3xl border border-[#D9E8F6] bg-[#F8FAFF] p-4 shadow-xl lg:inset-x-auto lg:inset-y-0 lg:right-0 lg:w-[360px] lg:max-h-none lg:rounded-none">
          <div className="mb-4 flex items-center justify-between gap-2">
            <div role="tablist" aria-label="Panel" className="flex gap-1 rounded-full border border-[#D9E8F6] bg-white p-1">
              {tabs.map((t) => (
                <button key={t.id} type="button" role="tab" aria-selected={panel === t.id} onClick={() => setPanel(t.id)}
                  className={`${TAB} ${panel === t.id ? 'bg-[#EAF6FF] font-medium text-[#8B3158]' : 'text-[#617698] hover:text-[#23314F]'}`}>
                  {t.label}
                </button>
              ))}
            </div>
            <button type="button" onClick={() => setPanel(null)} aria-label="Close panel"
              className="flex h-8 w-8 items-center justify-center rounded-full text-[#7183A3] transition-colors hover:bg-white hover:text-[#23314F]">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          {panel === 'mind' && owner ? <MindPanel state={inner} thoughts={thoughts} /> : panel === 'game' ? <DesktopSessionPanel /> : <>
            <p className="mb-3 text-xs text-[#7183A3]">Your shared screen is separate from the game session’s view.</p>
            <SenseStrip awareness={awareness} hearingMuted={voice.muted} onToggleEye={eye.toggle} eyeOpening={eye.status === 'opening'} eyeError={eye.error} />
            <button type="button" className={`${CHIP} mt-3 w-full`} disabled={eye.status === 'opening'} onClick={eye.toggle}>{eye.status === 'open' ? 'Stop sharing screen' : 'Share screen'}</button>
            <div className="mt-3 rounded-2xl border border-[#D9E8F6] bg-white p-4 text-xs text-[#617698]">
              <p>Microphone: {ears}</p>
              {voice.state !== 'off' && <button type="button" className={`${CHIP} mt-3`} disabled={inputBlocked} onClick={() => voice.setMuted(!voice.muted)}>{voice.muted ? 'Unmute microphone' : 'Mute microphone'}</button>}
              <p className="mt-3">{voice.muted ? 'Microphone input is muted. Voice replies can still play.' : voice.wakeWordAvailable ? 'Wake-word listening is available while the microphone is on.' : 'Wake-word listening is unavailable. Use voice to talk directly.'}</p>
            </div>
          </>}
        </aside>
      </>}
    </>
  )
}
