import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Logo } from '../components/Logo'
import { Button } from '../components/Button'
import { useAuth } from '../hooks/useAuth'
import { ApiError } from '../api/client'

type Mode = 'signin' | 'signup'

const inputClass =
  'w-full rounded-md border border-line bg-surface-2 px-3.5 py-2.5 text-sm text-fg placeholder:text-muted/60 outline-none transition-colors focus:border-accent'

export function LoginPage() {
  const [params] = useSearchParams()
  const [mode, setMode] = useState<Mode>(
    params.get('mode') === 'signup' ? 'signup' : 'signin',
  )
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const { signin, signup } = useAuth()
  const navigate = useNavigate()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      if (mode === 'signup') await signup(name, email, password)
      else await signin(email, password)
      navigate('/chat')
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Something went wrong. Try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  function switchMode(next: Mode) {
    setMode(next)
    setError(null)
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-ink px-6">
      <div className="bg-grid pointer-events-none absolute inset-0 opacity-50" />
      <div className="glow pointer-events-none absolute top-1/2 left-1/2 h-[500px] w-[500px] -translate-x-1/2 -translate-y-1/2" />

      <div className="relative w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>

        <div className="rounded-2xl border border-line bg-surface/70 p-7 backdrop-blur-sm">
          {/* mode toggle */}
          <div className="mb-6 grid grid-cols-2 gap-1 rounded-lg border border-line bg-surface-2 p-1">
            {(['signin', 'signup'] as Mode[]).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => switchMode(m)}
                className={`rounded-md py-1.5 text-sm font-medium transition-colors ${
                  mode === m ? 'bg-line-bright text-fg' : 'text-muted hover:text-fg'
                }`}
              >
                {m === 'signin' ? 'Sign in' : 'Sign up'}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === 'signup' && (
              <div>
                <label htmlFor="signup-name" className="mb-1.5 block font-mono text-[11px] tracking-wider text-muted uppercase">
                  Name
                </label>
                <input
                  id="signup-name"
                  className={inputClass}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Jane Doe"
                  required
                  autoComplete="name"
                />
              </div>
            )}

            <div>
              <label htmlFor="auth-email" className="mb-1.5 block font-mono text-[11px] tracking-wider text-muted uppercase">
                Email
              </label>
              <input
                id="auth-email"
                className={inputClass}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
                autoComplete="email"
              />
            </div>

            <div>
              <label htmlFor="auth-password" className="mb-1.5 block font-mono text-[11px] tracking-wider text-muted uppercase">
                Password
              </label>
              <input
                id="auth-password"
                className={inputClass}
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
                minLength={8}
                autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
              />
            </div>

            {error && (
              <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {error}
              </p>
            )}

            <Button
              type="submit"
              variant="primary"
              className="w-full py-3"
              disabled={submitting}
            >
              {submitting
                ? 'Working…'
                : mode === 'signup'
                  ? 'Create account'
                  : 'Sign in'}
            </Button>
          </form>
        </div>

        <p className="mt-6 text-center text-xs text-muted">
          <Link to="/" className="transition-colors hover:text-fg">
            ← Back to home
          </Link>
        </p>
      </div>
    </div>
  )
}
