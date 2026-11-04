import type { ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { InitialAvatar } from './Avatar'
import { tabsFor } from './tabs'

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
    <>
      <aside className="hidden w-56 shrink-0 flex-col border-r border-[#EAF0FA] bg-white px-4 py-7 md:flex">
        <Link to="/dashboard" className="flex items-center gap-2 px-2 text-[17px] font-semibold tracking-[-0.02em] text-[#23314F]">
          <span className="flex h-6 w-6 items-center justify-center text-[18px]">✦</span>
          Jophiel
        </Link>
        <p className="mt-1 px-2 text-[10px] text-[#7183A3]">Your nutrition companion</p>

        <nav className="mt-12 flex flex-col gap-2" aria-label="Main navigation">
          {tabsFor(user).map((tab) => {
            const active = tab.label === 'History' ? pathname === '/history' : pathname === tab.to
            return (
              <Link
                key={tab.label}
                to={tab.label === 'History' ? '/chat' : tab.to}
                aria-current={active ? 'page' : undefined}
                className={`rounded-full px-3.5 py-2.5 text-[13px] transition-colors ${active ? 'bg-[#EAF6FF] font-medium text-[#8B3158]' : 'text-[#617698] hover:bg-[#F4F8FD] hover:text-[#23314F]'}`}
              >
                {tab.label}
              </Link>
            )
          })}
        </nav>

        <div className="mt-10 border-t border-[#EAF0FA] pt-7">
          <p className="px-2 text-[9px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase">Profile</p>
          <div className="mt-4 flex items-center gap-2 px-2">
            <span className="h-8 w-8 overflow-hidden rounded-full border border-[#DCCFF2] bg-[#F2EFFB]">
              <InitialAvatar name={user?.name} />
            </span>
            <p className="text-[13px] font-medium text-[#23314F]">{user?.name ?? 'Profile'}</p>
          </div>
        </div>

        <div className="mt-auto flex flex-col gap-2 px-2 text-[11px] text-[#7183A3]">
          {children}
          <button onClick={handleSignout} className="text-left transition-colors hover:text-[#8B3158]">Sign out</button>
        </div>
      </aside>

      <header className="flex flex-col gap-3 border-b border-[#EAF0FA] bg-white/95 px-4 py-3 text-[#23314F] backdrop-blur-sm md:hidden">
        <div className="flex items-center justify-between">
          <Link to="/dashboard" className="flex items-center gap-2 text-[17px] font-semibold">✦ Jophiel</Link>
          <button onClick={handleSignout} className="text-[11px] text-[#7183A3]">Sign out</button>
        </div>
        <nav className="flex gap-4 overflow-x-auto" aria-label="Main navigation">
          {tabsFor(user).map((tab) => (
            <Link key={tab.label} to={tab.label === 'History' ? '/chat' : tab.to} className={`whitespace-nowrap text-[11px] uppercase tracking-[0.08em] ${pathname === tab.to && tab.label !== 'History' ? 'text-[#8B3158]' : 'text-[#7183A3]'}`}>
              {tab.label}
            </Link>
          ))}
        </nav>
      </header>
    </>
  )
}
