import { Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from '../hooks/useAuth'

/** ``ownerOnly``: her insides (the backend refuses everyone else); other users go home. */
export function ProtectedRoute({ children, ownerOnly = false }: { children: ReactNode; ownerOnly?: boolean }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-ink">
        <span className="font-mono text-xs tracking-widest text-muted uppercase">
          Authenticating…
        </span>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search + location.hash }} />
  }

  if (ownerOnly && !user.is_owner) {
    return <Navigate to="/dashboard" replace />
  }

  return <>{children}</>
}
