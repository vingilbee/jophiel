import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Button } from '../components/Button'
import { Feathers } from '../components/Feathers'
import { useAuth } from '../hooks/useAuth'
import { ApiError } from '../api/client'

type Mode = 'signin' | 'signup'

const inputClass =
  'w-full rounded-xl border border-[#D9E8F6] bg-[#FBFDFF] px-3.5 py-3 text-sm text-[#23314F] placeholder:text-[#9AAAC1] outline-none transition-colors focus:border-[#8FC7E8]'

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
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/dashboard'

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      if (mode === 'signup') await signup(name, email, password)
      else await signin(email, password)
      navigate(from, { replace: true })
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
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#F8FAFF] px-6 py-8 text-[#23314F]">
      <Feathers className="absolute -top-8 -left-16 w-72 rotate-[-18deg] opacity-80" />
      <Feathers className="absolute -right-20 -bottom-10 w-72 rotate-[165deg] opacity-65" />

      <div className="relative grid w-full max-w-5xl overflow-hidden rounded-[28px] border border-[#D9E8F6] bg-white shadow-[0_18px_60px_rgba(35,49,79,0.10)] lg:grid-cols-[.9fr_1.1fr]">
        <div className="relative hidden min-h-[610px] overflow-hidden bg-gradient-to-br from-[#B9DCF4] via-[#EAF6FF] to-[#D9D6F3] p-10 lg:flex lg:flex-col lg:justify-end">
          <div className="absolute inset-0 bg-gradient-to-t from-[#EAF6FF] via-[#EAF6FF]/25 to-transparent" />
          <Feathers className="absolute inset-0 h-full w-full opacity-25" />
          <div className="relative z-10 max-w-xs">
            <p className="text-2xl font-semibold leading-tight text-[#23314F]">Nourish today,<br />a brighter tomorrow.</p>
            <p className="mt-3 text-sm text-[#526A8F]">A quiet companion for the choices that shape how you feel.</p>
          </div>
        </div>

        <div className="relative w-full max-w-md justify-self-center p-7 sm:p-10">
          <div className="mb-8 flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-full border border-[#B9DCF4] bg-[#F2EFFB] text-[#8B3158]">✦</span>
            <span className="text-lg font-semibold text-[#23314F]">Jophiel</span>
          </div>

          <div className="mb-6">
            <h1 className="text-2xl font-semibold tracking-[-0.03em]">{mode === 'signin' ? 'Welcome back' : 'Begin your journey'}</h1>
            <p className="mt-1.5 text-sm text-[#7183A3]">Good things start with small choices.</p>
          </div>

        <div className="rounded-2xl border border-[#EAF0FA] bg-white p-1">
          {/* mode toggle */}
          <div className="mb-6 grid grid-cols-2 gap-1 rounded-xl border border-[#D9E8F6] bg-[#F8FAFF] p-1">
            {(['signin', 'signup'] as Mode[]).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => switchMode(m)}
                className={`rounded-md py-1.5 text-sm font-medium transition-colors ${
                  mode === m ? 'bg-white text-[#8B3158] shadow-sm' : 'text-[#7183A3] hover:text-[#23314F]'
                }`}
              >
                {m === 'signin' ? 'Sign in' : 'Sign up'}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === 'signup' && (
              <div>
                <label htmlFor="signup-name" className="mb-1.5 block text-[11px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase">
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
              <label htmlFor="auth-email" className="mb-1.5 block text-[11px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase">
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
              <label htmlFor="auth-password" className="mb-1.5 block text-[11px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase">
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
              <p className="rounded-xl border border-[#E7B5C5] bg-[#FBEFF3] px-3 py-2 text-xs text-[#8B3158]">
                {error}
              </p>
            )}

            <Button
              type="submit"
              variant="primary"
              className="w-full rounded-xl bg-[#8B3158] py-3 text-white shadow-[0_8px_20px_rgba(139,49,88,0.20)] hover:bg-[#762847]"
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

        <p className="mt-6 text-center text-xs text-[#7183A3]">
          <Link to="/" className="transition-colors hover:text-[#8B3158]">
            ← Back to home
          </Link>
        </p>
      </div>
    </div>
    </div>
  )
}
