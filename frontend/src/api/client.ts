// Centralized fetch wrapper. All requests go through Vite's /api proxy to the
// backend and include the session cookie.
const BASE = '/api'

export class ApiError extends Error {
  status: number
  /** Stable refusal code, when the endpoint sends `{detail: {code, message}}`. */
  code: string | null
  constructor(status: number, message: string, code: string | null = null) {
    super(message)
    this.status = status
    this.code = code
    this.name = 'ApiError'
  }
}

async function parseError(res: Response): Promise<never> {
  let detail = res.statusText
  let code: string | null = null
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') detail = body.detail
    else if (typeof body?.detail?.message === 'string') {
      detail = body.detail.message
      if (typeof body.detail.code === 'string') code = body.detail.code
    }
  } catch {
    /* non-JSON error body — keep statusText */
  }
  throw new ApiError(res.status, detail, code)
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
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  /** Raw fetch (same proxy + cookies) — used for streaming responses. */
  raw: (path: string, init?: RequestInit) =>
    fetch(BASE + path, { credentials: 'include', ...init }),
}
