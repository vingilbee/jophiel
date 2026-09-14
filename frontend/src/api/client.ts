// Centralized fetch wrapper. All requests go through Vite's /api proxy to the
// backend and include the session cookie.
const BASE = '/api'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

async function parseError(res: Response): Promise<never> {
  let detail = res.statusText
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') detail = body.detail
  } catch {
    /* non-JSON error body — keep statusText */
  }
  throw new ApiError(res.status, detail)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (!res.ok) await parseError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  /** Raw fetch (same proxy + cookies) — used for streaming responses. */
  raw: (path: string, init?: RequestInit) =>
    fetch(BASE + path, { credentials: 'include', ...init }),
}
