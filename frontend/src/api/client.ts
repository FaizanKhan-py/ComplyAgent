import type {
  ActivityEvent,
  AdminConfig,
  AssistantReply,
  AssistantRequest,
  AuthStatus,
  CustomerCreate,
  CustomerCreated,
  CustomerDetail,
  CustomerGuidance,
  CustomerMessage,
  CustomerList,
  CustomerQuery,
  DatasetPrediction,
  DeleteResult,
  Health,
  KnowledgeBaseStats,
  PolicyDocument,
  PortfolioSummary,
  PredictionModelInfo,
  QueryRequest,
  QueryResponse,
  RegisterRequest,
  SessionInfo,
  User,
} from './types'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
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

/**
 * The session itself is an HttpOnly cookie that scripts cannot read. This token only proves a
 * request was made by this page (CSRF protection); it is kept in memory, never in storage, and
 * is fetched again from /api/auth/me after a reload.
 */
let csrfToken: string | null = null

let onUnauthorized: (() => void) | null = null

/** Called when the API says the session is gone, so the app can return to the sign-in page. */
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler
}

interface RequestOptions {
  method?: string
  json?: unknown
  body?: FormData
  /** False for the sign-in calls themselves, where a 401 is an answer and not a lost session. */
  session?: boolean
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = {}
  if (csrfToken && method !== 'GET') headers['X-CSRF-Token'] = csrfToken
  if (options.json !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers,
      // The cookie must travel when the API is on another origin (VITE_API_BASE_URL).
      credentials: BASE_URL ? 'include' : 'same-origin',
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
  if (response.status === 401 && options.session !== false) {
    csrfToken = null
    onUnauthorized?.()
  }
  throw new ApiError(response.status, code, message)
}

async function startSession(pending: Promise<SessionInfo>): Promise<User> {
  const session = await pending
  csrfToken = session.csrf_token
  return session.user
}

export const api = {
  authStatus: () => request<AuthStatus>('/api/auth/status', { session: false }),
  /** The signed-in user, or a 401 ApiError when there is no valid session. */
  me: () => startSession(request<SessionInfo>('/api/auth/me', { session: false })),
  login: (email: string, password: string) =>
    startSession(
      request<SessionInfo>('/api/auth/login', {
        method: 'POST',
        json: { email, password },
        session: false,
      }),
    ),
  register: (body: RegisterRequest) =>
    request<User>('/api/auth/register', { method: 'POST', json: body, session: false }),
  logout: async () => {
    try {
      await request<{ ok: boolean }>('/api/auth/logout', { method: 'POST', session: false })
    } finally {
      csrfToken = null
    }
  },

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

  portfolioSummary: () => request<PortfolioSummary>('/api/admin/portfolio/summary'),
  listCustomers: (query: CustomerQuery = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== '') params.set(key, String(value))
    }
    const suffix = params.size ? `?${params}` : ''
    return request<CustomerList>(`/api/admin/customers${suffix}`)
  },
  getCustomer: (id: number) => request<CustomerDetail>(`/api/admin/customers/${id}`),
  createCustomer: (body: CustomerCreate) =>
    request<CustomerCreated>('/api/admin/customers', { method: 'POST', json: body }),
  customerGuidance: (id: number, signal?: AbortSignal) =>
    request<CustomerGuidance>(`/api/admin/customers/${id}/guidance`, { method: 'POST', signal }),
  /** A checked draft from the Decision Agent and the message writer. Nothing is sent. */
  generateMessage: (id: number, signal?: AbortSignal) =>
    request<CustomerMessage>(`/api/admin/customers/${id}/message`, { method: 'POST', signal }),

  predictionModel: () => request<PredictionModelInfo>('/api/admin/predict/model'),
  predictDataset: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<DatasetPrediction>('/api/admin/predict/dataset', { method: 'POST', body })
  },

  query: (body: QueryRequest, signal?: AbortSignal) =>
    request<QueryResponse>('/api/admin/rag/query', { method: 'POST', json: body, signal }),

  /** Ask the AI Assistant (any signed-in user). The server keeps the conversation. */
  assistantChat: (body: AssistantRequest, signal?: AbortSignal) =>
    request<AssistantReply>('/api/assistant/chat', { method: 'POST', json: body, signal }),
}
