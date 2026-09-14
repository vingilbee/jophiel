import { api, ApiError } from './client'
import type { User } from '../types'

export function signup(name: string, email: string, password: string) {
  return api.post<User>('/auth/signup', { name, email, password })
}

export function signin(email: string, password: string) {
  return api.post<User>('/auth/signin', { email, password })
}

export function signout() {
  return api.post<void>('/auth/signout')
}

/** Current user, or null if not authenticated. */
export async function getMe(): Promise<User | null> {
  try {
    return await api.get<User>('/auth/me')
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null
    throw err
  }
}
