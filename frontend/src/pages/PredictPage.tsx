import { useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import { AppHeader } from '../components/AppHeader'
import { Button } from '../components/Button'
import { predictMacros } from '../api/predict'
import type { Prediction, PredictionMode } from '../types'

const MODES: { value: PredictionMode; label: string; hint: string }[] = [
  { value: 'combined', label: 'Combined', hint: 'VLM + depth model · most accurate' },
  { value: 'regression', label: 'Fast', hint: 'Depth model only · quicker' },
]

const MAX_MB = 10

export function PredictPage() {
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [mode, setMode] = useState<PredictionMode>('combined')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<Prediction | null>(null)
  const [dragging, setDragging] = useState(false)

  const inputRef = useRef<HTMLInputElement>(null)

  // Revoke the object URL when the preview changes / unmounts to avoid leaks.
  useEffect(() => {
    if (!previewUrl) return
    return () => URL.revokeObjectURL(previewUrl)
  }, [previewUrl])

  function selectFile(next: File | null) {
    setError(null)
    setResult(null)
    if (!next) return
    if (!next.type.startsWith('image/')) {
      setError('Please choose an image file.')
      return
    }
    if (next.size > MAX_MB * 1024 * 1024) {
      setError(`Image is too large (max ${MAX_MB} MB).`)
      return
    }
    setFile(next)
    setPreviewUrl(URL.createObjectURL(next))
  }

  function handleInput(e: ChangeEvent<HTMLInputElement>) {
    selectFile(e.target.files?.[0] ?? null)
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    selectFile(e.dataTransfer.files?.[0] ?? null)
  }

  function reset() {
    setFile(null)
    setPreviewUrl(null)
    setResult(null)
    setError(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  async function analyze() {
    if (!file || loading) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      setResult(await predictMacros(file, mode))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Prediction failed. Try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen flex-col bg-ink text-fg">
      <AppHeader />

      {/* body */}
      <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-8">
        <div className="mb-6">
          <h1 className="text-xl font-medium">Scan a meal</h1>
          <p className="mt-1 text-sm text-muted">
            Upload or snap a photo of your food and get an estimated macro
            breakdown.
          </p>
        </div>

        {/* mode toggle */}
        <div className="mb-5 flex gap-2">
          {MODES.map((m) => (
            <button
              key={m.value}
              onClick={() => setMode(m.value)}
              disabled={loading}
              title={m.hint}
              className={`flex-1 rounded-lg border px-3 py-2 text-left transition-colors disabled:opacity-50 ${
                mode === m.value
                  ? 'border-accent bg-accent/10'
                  : 'border-line bg-surface/50 hover:border-line-bright'
              }`}
            >
              <span className="block text-sm font-medium text-fg">{m.label}</span>
              <span className="block font-mono text-[10px] tracking-wider text-muted uppercase">
                {m.hint}
              </span>
            </button>
          ))}
        </div>

        {/* dropzone / preview */}
        {!previewUrl ? (
          <label
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            className={`flex cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed px-6 py-16 text-center transition-colors ${
              dragging
                ? 'border-accent bg-accent/5'
                : 'border-line-bright bg-surface/40 hover:border-accent/60'
            }`}
          >
            <span className="flex h-12 w-12 items-center justify-center rounded-xl border border-line-bright bg-surface-2">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                <path
                  d="M12 16V4m0 0L7 9m5-5l5 5M5 20h14"
                  stroke="var(--color-accent)"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <span className="mt-4 text-sm text-fg">
              Drop an image, or <span className="text-accent">browse</span>
            </span>
            <span className="mt-1 font-mono text-[10px] tracking-wider text-muted uppercase">
              JPG / PNG · up to {MAX_MB} MB
            </span>
            <input
              ref={inputRef}
              type="file"
              accept="image/*"
              capture="environment"
              onChange={handleInput}
              className="hidden"
            />
          </label>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-line bg-surface/40">
            <img
              src={previewUrl}
              alt="Food preview"
              className="max-h-80 w-full object-contain bg-ink"
            />
            <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3">
              <span className="truncate font-mono text-xs text-muted">
                {file?.name}
              </span>
              <div className="flex shrink-0 gap-2">
                <Button variant="ghost" onClick={reset} disabled={loading}>
                  Remove
                </Button>
                <Button variant="primary" onClick={() => void analyze()} disabled={loading}>
                  {loading ? 'Analyzing…' : 'Analyze'}
                </Button>
              </div>
            </div>
          </div>
        )}

        {error && (
          <p className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        )}

        {/* result */}
        {result && <MacroResult result={result} />}
      </main>
    </div>
  )
}

function MacroResult({ result }: { result: Prediction }) {
  const macros = [
    { label: 'Calories', value: result.calories, unit: 'kcal' },
    { label: 'Protein', value: result.protein, unit: 'g' },
    { label: 'Carbs', value: result.carbs, unit: 'g' },
    { label: 'Fat', value: result.fat, unit: 'g' },
  ]
  return (
    <section className="mt-6 rounded-2xl border border-line bg-surface/50 p-5">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium text-fg">
            {result.dish && result.dish !== 'unknown'
              ? result.dish
              : 'Estimated macros'}
          </h2>
          <p className="mt-0.5 font-mono text-[10px] tracking-wider text-muted uppercase">
            {result.source === 'combined' ? 'VLM + depth model' : 'Depth model'}
            {result.portion_scale != null &&
              ` · portion ×${result.portion_scale.toFixed(2)}`}
          </p>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {macros.map((m) => (
          <div
            key={m.label}
            className="rounded-xl border border-line bg-surface-2 px-4 py-3 text-center"
          >
            <div className="text-2xl font-semibold text-accent">
              {Math.round(m.value)}
            </div>
            <div className="mt-0.5 font-mono text-[10px] tracking-wider text-muted uppercase">
              {m.label} · {m.unit}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-4 text-center text-[11px] text-muted/70">
        Estimates only — verify before logging.
      </p>
    </section>
  )
}
