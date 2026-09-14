import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import * as authApi from '../api/auth'
import type { User } from '../types'

interface AuthContextValue {
  user: User | null
  loading: boolean
  signin: (email: string, password: string) => Promise<void>
  signup: (name: string, email: string, password: string) => Promise<void>
  signout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    authApi
      .getMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  const signin = useCallback(async (email: string, password: string) => {
    setUser(await authApi.signin(email, password))
  }, [])

  const signup = useCallback(
    async (name: string, email: string, password: string) => {
      setUser(await authApi.signup(name, email, password))
    },
    [],
  )

  const signout = useCallback(async () => {
    await authApi.signout()
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, signin, signup, signout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
