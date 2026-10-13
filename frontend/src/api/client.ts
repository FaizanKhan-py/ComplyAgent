import type {
  ActivityEvent,
  AdminConfig,
  DeleteResult,
  Health,
  KnowledgeBaseStats,
  PolicyDocument,
  QueryRequest,
  QueryResponse,
} from './types'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const TOKEN_KEY = 'complyagent.adminToken'

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

/** The admin token lives in sessionStorage: it is gone when the tab closes. */
export const tokenStore = {
  get(): string | null {
    try {
      return sessionStorage.getItem(TOKEN_KEY)
    } catch {
      return null
    }
  },
  set(token: string) {
    try {
      sessionStorage.setItem(TOKEN_KEY, token)
    } catch {
      /* storage unavailable: the token lasts for this page load only */
    }
  },
  clear() {
    try {
      sessionStorage.removeItem(TOKEN_KEY)
    } catch {
      /* nothing to clear */
    }
  },
}

let onUnauthorized: (() => void) | null = null

/** Called when the API rejects the stored token, so the app can return to the sign-in screen. */
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler
}

interface RequestOptions {
  method?: string
  json?: unknown
  body?: FormData
  token?: string | null
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const usesStoredToken = options.token === undefined
  const token = usesStoredToken ? tokenStore.get() : options.token
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  if (options.json !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.json !== undefined ? JSON.stringify(options.json) : options.body,
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(
      0,
      'network_error',
      'Cannot reach the ComplyAgent API. Check that the backend is running.',
    )
  }

  if (response.ok) return (await response.json()) as T

  let code = 'http_error'
  let message = `The server returned an error (HTTP ${response.status}).`
  try {
    const body = await response.json()
    if (typeof body?.error?.message === 'string') {
      code = body.error.code ?? code
      message = body.error.message
    }
  } catch {
    // Not JSON: typically the dev proxy answering because the backend is down.
    if (response.status >= 500) {
      code = 'network_error'
      message = 'Cannot reach the ComplyAgent API. Check that the backend is running.'
    }
  }
  if (response.status === 401 && usesStoredToken) onUnauthorized?.()
  throw new ApiError(response.status, code, message)
}

export const api = {
  authStatus: () => request<{ auth_required: boolean }>('/api/auth/status', { token: null }),
  verifyToken: (token: string) => request<{ ok: boolean }>('/api/admin/session', { token }),

  health: () => request<Health>('/api/admin/health'),
  config: () => request<AdminConfig>('/api/admin/config'),

  listDocuments: async () =>
    (await request<{ documents: PolicyDocument[] }>('/api/admin/documents')).documents,
  uploadDocument: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<PolicyDocument>('/api/admin/documents', { method: 'POST', body })
  },
  deleteDocument: (id: string) =>
    request<DeleteResult>(`/api/admin/documents/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  reindexDocument: (id: string) =>
    request<PolicyDocument>(`/api/admin/documents/${encodeURIComponent(id)}/reindex`, {
      method: 'POST',
    }),
  activity: async (limit = 10) =>
    (await request<{ events: ActivityEvent[] }>(`/api/admin/activity?limit=${limit}`)).events,

  stats: () => request<KnowledgeBaseStats>('/api/admin/kb/stats'),
  rebuild: () => request<{ queued: number }>('/api/admin/kb/rebuild', { method: 'POST' }),

  query: (body: QueryRequest, signal?: AbortSignal) =>
    request<QueryResponse>('/api/admin/rag/query', { method: 'POST', json: body, signal }),
}
