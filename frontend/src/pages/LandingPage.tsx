import { Link } from 'react-router-dom'
import { Logo } from '../components/Logo'
import { Button } from '../components/Button'
import { useAuth } from '../hooks/useAuth'

const FEATURES = [
  {
    tag: '01',
    title: 'Photo → Macros',
    body: 'Snap a meal. A trained model predicts calories, protein, carbs, and fat in seconds.',
  },
  {
    tag: '02',
    title: 'Nutrition Chatbot',
    body: 'Ask anything about diet and macros. Get clear, practical answers in real time.',
  },
  {
    tag: '03',
    title: 'Track Over Time',
    body: 'Log weight and intake. Watch the trends that actually move your goals.',
  },
]

export function LandingPage() {
  const { user } = useAuth()

  return (
    <div className="relative min-h-screen overflow-hidden bg-ink text-fg">
      {/* backdrop */}
      <div className="bg-grid pointer-events-none absolute inset-0 opacity-60" />
      <div className="glow pointer-events-none absolute -top-40 left-1/2 h-[600px] w-[600px] -translate-x-1/2" />

      <div className="relative mx-auto flex min-h-screen max-w-6xl flex-col px-6">
        {/* nav */}
        <header className="flex items-center justify-between py-6">
          <Logo />
          <nav className="flex items-center gap-3">
            {user ? (
              <>
                <Link to="/predict">
                  <Button variant="ghost">Scan food</Button>
                </Link>
                <Link to="/chat">
                  <Button variant="outline">Open Chat</Button>
                </Link>
              </>
            ) : (
              <>
                <Link to="/login">
                  <Button variant="ghost">Sign in</Button>
                </Link>
                <Link to="/login?mode=signup">
                  <Button variant="primary">Get started</Button>
                </Link>
              </>
            )}
          </nav>
        </header>

        {/* hero */}
        <main className="flex flex-1 flex-col items-center justify-center py-20 text-center">
          <span className="mb-6 inline-flex items-center gap-2 rounded-full border border-line bg-surface/60 px-3 py-1 font-mono text-[11px] tracking-[0.2em] text-muted uppercase">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse-dot" />
            Macronutrient intelligence
          </span>

          <h1 className="max-w-3xl text-5xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
            Know what's on your plate.
            <br />
            <span className="text-muted">Down to the gram.</span>
          </h1>

          <p className="mt-6 max-w-xl text-base leading-relaxed text-muted">
            MacroVision turns a photo of your food into a macro breakdown — and
            pairs it with a nutrition chatbot and tracking dashboard to keep you
            on target.
          </p>

          <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
            <Link to={user ? '/chat' : '/login?mode=signup'}>
              <Button variant="primary" className="px-6 py-3">
                {user ? 'Open the chatbot' : 'Start for free'}
              </Button>
            </Link>
            <Link to="/chat">
              <Button variant="outline" className="px-6 py-3">
                Try the chatbot →
              </Button>
            </Link>
          </div>
        </main>

        {/* features */}
        <section className="grid gap-4 pb-24 sm:grid-cols-3">
          {FEATURES.map((f) => (
            <div
              key={f.tag}
              className="group rounded-xl border border-line bg-surface/50 p-6 transition-colors hover:border-line-bright"
            >
              <span className="font-mono text-xs text-accent">{f.tag}</span>
              <h3 className="mt-3 text-lg font-medium">{f.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{f.body}</p>
            </div>
          ))}
        </section>

        <footer className="border-t border-line py-6 text-center font-mono text-xs tracking-wider text-muted">
          MACROVISION · BUILT FOR TESTING THE AGENT
        </footer>
      </div>
    </div>
  )
}
