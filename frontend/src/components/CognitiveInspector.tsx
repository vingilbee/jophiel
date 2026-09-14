import { useEffect, useState } from 'react'
import { chatApi } from '../api/chat'
import type { CognitiveInspection, CognitiveLLMInspection } from '../types'

interface CognitiveInspectorProps {
  open: boolean
  draft: string
  recentMessages: string[]
  imageCount: number
  assetCount: number
  onClose: () => void
}

function Meter({ label, value }: { label: string; value: number }) {
  const bounded = Math.max(0, Math.min(1, value))
  return (
    <div>
      <div className="mb-1 flex justify-between font-mono text-[10px] tracking-wider text-muted uppercase">
        <span>{label}</span>
        <span>{bounded.toFixed(3)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-line">
        <div className="h-full rounded-full bg-accent" style={{ width: `${bounded * 100}%` }} />
      </div>
    </div>
  )
}

function Flag({ label, value }: { label: string; value: boolean }) {
  return (
    <div className="flex items-center justify-between rounded-md border border-line bg-surface px-2.5 py-2">
      <span className="text-xs text-muted">{label}</span>
      <span className={`font-mono text-[10px] uppercase ${value ? 'text-accent' : 'text-muted/50'}`}>
        {String(value)}
      </span>
    </div>
  )
}

export function CognitiveInspector({
  open,
  draft,
  recentMessages,
  imageCount,
  assetCount,
  onClose,
}: CognitiveInspectorProps) {
  const [message, setMessage] = useState(draft)
  const [result, setResult] = useState<CognitiveInspection | CognitiveLLMInspection | null>(null)
  const [mode, setMode] = useState<'rules' | 'llm'>('llm')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open && draft.trim()) setMessage(draft)
  }, [open, draft])

  if (!open) return null

  async function runInspection() {
    const text = message.trim()
    if (!text || loading) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      setResult(await chatApi.inspectCognition({
        mode,
        message: text,
        recent_messages: recentMessages.slice(-24),
        image_count: imageCount,
        asset_count: assetCount,
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cognitive inspection failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <aside className="flex h-full w-screen max-w-[380px] shrink-0 flex-col border-l border-line bg-surface-2/95 shadow-2xl backdrop-blur-sm">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div>
          <p className="font-mono text-[11px] tracking-wider text-accent uppercase">Cognitive test</p>
          <p className="mt-0.5 text-[11px] text-muted">Diagnostic · proposed actions are not executed</p>
        </div>
        <button onClick={onClose} className="text-lg text-muted hover:text-fg" aria-label="Close cognitive inspector">×</button>
      </div>

      <div className="border-b border-line p-4">
        <select
          aria-label="Cognitive evaluation mode"
          value={mode}
          disabled={loading}
          onChange={(event) => { setMode(event.target.value as 'rules' | 'llm'); setResult(null); setError(null) }}
          className="mb-3 w-full rounded-lg border border-line bg-ink p-2 text-xs text-fg"
        >
          <option value="llm">LLM · perception → interpretation → action</option>
          <option value="rules">Rules · perception → interpretation</option>
        </select>
        {mode === 'llm' && <p className="mb-2 text-xs text-muted">One model call using this message and recent conversation. Attachments are represented by counts only.</p>}
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          rows={4}
          placeholder="Enter a message to inspect…"
          className="w-full resize-none rounded-lg border border-line bg-ink p-3 text-xs text-fg outline-none placeholder:text-muted/50 focus:border-line-bright"
        />
        <button
          type="button"
          onClick={() => void runInspection()}
          disabled={!message.trim() || loading}
          className="mt-2 w-full rounded-lg bg-accent px-3 py-2 font-mono text-[11px] tracking-wider text-ink uppercase disabled:opacity-40"
        >
          {loading ? 'Processing…' : 'Inspect cognition'}
        </button>
        {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        {!result ? (
          <p className="text-xs leading-relaxed text-muted">Run a message to inspect its structured cognitive input.</p>
        ) : 'state' in result ? (
          <>
            {Object.entries(result.state).map(([stage, fields]) => (
              <section key={stage}>
                <h3 className="mb-2 font-mono text-[10px] tracking-widest text-muted uppercase">{stage}</h3>
                <dl className="space-y-3 rounded-lg border border-line bg-ink/50 p-3 text-xs">
                  {Object.entries(fields).map(([name, value]) => (
                    <div key={name}>
                      <dt className="mb-1 text-muted">{name.replaceAll('_', ' ')}</dt>
                      <dd className="whitespace-pre-wrap break-words text-fg">{Array.isArray(value) ? value.join('\n') || 'None' : value ?? 'None'}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
            <p className="text-xs text-muted">{result.metrics.processing_ms.toFixed(0)} ms · {result.metrics.online_model_calls} model call · {result.metrics.recent_message_count} history items · {result.image_count} images · {result.asset_count} assets</p>
          </>
        ) : (
          <>
            <section className="space-y-2">
              <h3 className="font-mono text-[10px] tracking-widest text-muted uppercase">Perception</h3>
              <div className="grid grid-cols-2 gap-2">
                <Flag label="Question" value={result.perception.is_question} />
                <Flag label="Request" value={result.perception.is_request} />
                <Flag label="Directed" value={result.perception.is_directed_at_agent} />
                <Flag label="Clarify" value={result.interpretation.needs_clarification} />
              </div>
              <div className="space-y-3 rounded-lg border border-line bg-ink/50 p-3">
                <Meter label="Novelty" value={result.perception.novelty} />
                <Meter label="Ambiguity" value={result.perception.ambiguity} />
              </div>
            </section>

            <section>
              <h3 className="mb-2 font-mono text-[10px] tracking-widest text-muted uppercase">Interpretation</h3>
              <div className="rounded-lg border border-line bg-ink/50 p-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-sm font-medium capitalize text-fg">{result.interpretation.intent}</span>
                  <span className="font-mono text-xs text-accent">{result.interpretation.confidence.toFixed(3)}</span>
                </div>
                <p className="mt-1 font-mono text-[10px] text-muted">source: {result.interpretation.source}</p>
                {result.interpretation.alternatives.length > 0 && (
                  <div className="mt-3 border-t border-line pt-2">
                    {result.interpretation.alternatives.map((alternative) => (
                      <div key={alternative.intent} className="flex justify-between text-xs text-muted">
                        <span className="capitalize">{alternative.intent}</span>
                        <span className="font-mono">{alternative.confidence.toFixed(3)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <section>
              <h3 className="mb-2 font-mono text-[10px] tracking-widest text-muted uppercase">Signals</h3>
              <div className="space-y-1.5">
                {Object.entries(result.perception.signals).map(([name, signal]) => (
                  <div key={name} className="rounded-md border border-line bg-ink/50 px-2.5 py-2">
                    <div className="flex justify-between gap-2 text-xs">
                      <span className="text-muted">{name}</span>
                      <span className="max-w-36 truncate font-mono text-fg" title={String(signal.value)}>{String(signal.value)}</span>
                    </div>
                    <div className="mt-1 flex justify-between font-mono text-[9px] text-muted/60">
                      <span>{signal.source}</span><span>confidence {signal.confidence.toFixed(2)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section>
              <h3 className="mb-2 font-mono text-[10px] tracking-widest text-muted uppercase">Runtime</h3>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-2 rounded-lg border border-line bg-ink/50 p-3 text-xs">
                <dt className="text-muted">Processing</dt><dd className="text-right font-mono">{result.metrics.processing_ms.toFixed(4)} ms</dd>
                <dt className="text-muted">Online calls</dt><dd className="text-right font-mono">{result.metrics.online_model_calls}</dd>
                <dt className="text-muted">Characters</dt><dd className="text-right font-mono">{result.metrics.character_count}</dd>
                <dt className="text-muted">Words</dt><dd className="text-right font-mono">{result.metrics.word_count}</dd>
                <dt className="text-muted">History items</dt><dd className="text-right font-mono">{result.metrics.recent_message_count}</dd>
                <dt className="text-muted">Schema</dt><dd className="text-right font-mono">v{result.perception.schema_version}</dd>
              </dl>
            </section>
          </>
        )}
      </div>
    </aside>
  )
}
