import type { ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Logo } from './Logo'
import { useAuth } from '../hooks/useAuth'

// The app's top-level tabs. Add a route here and it appears on every page.
const TABS = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/chat', label: 'Chat' },
  { to: '/predict', label: 'Scan' },
  { to: '/integrations', label: 'Integrations' },
]

/**
 * Shared header: logo, tab navigation, and the account controls.
 *
 * `children` renders as page-specific actions to the left of the account
 * block — e.g. Chat's "New chat" button and connection indicator.
 */
export function AppHeader({ children }: { children?: ReactNode }) {
  const { user, signout } = useAuth()
  const navigate = useNavigate()
  const { pathname } = useLocation()

  async function handleSignout() {
    await signout()
    navigate('/')
  }

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-surface/50 px-4 py-3 backdrop-blur-sm sm:flex-nowrap sm:px-5">
      <div className="min-w-0 shrink-0">
        <Logo to="/" />
      </div>
      <nav className="order-3 flex min-w-0 max-w-full basis-full items-center gap-4 overflow-x-auto pb-0.5 sm:order-none sm:basis-auto sm:gap-4">
          {TABS.map((tab) => {
            const active = pathname === tab.to
            return (
              <Link
                key={tab.to}
                to={tab.to}
                aria-current={active ? 'page' : undefined}
                className={`relative font-mono text-xs tracking-wider uppercase transition-colors ${
                  active ? 'text-accent' : 'text-muted hover:text-fg'
                }`}
              >
                {tab.label}
                {active && (
                  <span className="absolute -bottom-[13px] right-0 left-0 h-px bg-accent" />
                )}
              </Link>
            )
          })}
      </nav>

      <div className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-x-3 gap-y-1">
        {children}
        <span className="hidden font-mono text-xs tracking-wider text-muted sm:inline">
          {user?.name}
        </span>
        <button
          onClick={handleSignout}
          className="font-mono text-xs tracking-wider text-muted uppercase transition-colors hover:text-fg"
        >
          Sign out
        </button>
      </div>
    </header>
  )
}
