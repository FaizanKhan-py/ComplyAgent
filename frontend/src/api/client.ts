import type {
  ActivityEvent,
  AdminConfig,
  AssistantReply,
  AssistantRequest,
  AuditList,
  AuditQuery,
  AuthStatus,
  CollectionsDashboard,
  Conversation,
  CustomerCreate,
  CustomerCreated,
  CustomerDetail,
  CustomerGuidance,
  CustomerList,
  CustomerQuery,
  DatasetPrediction,
  DeleteResult,
  FollowUp,
  FollowUpRun,
  Inbox,
  Ledger,
  MailboxPoll,
  Health,
  KnowledgeBaseStats,
  PolicyDocument,
  PortfolioSummary,
  PredictionModelInfo,
  QueryRequest,
  QueryResponse,
  RegisterRequest,
  SessionInfo,
  SimulateRequest,
  Simulation,
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
  throw await failure(response, options.session !== false)
}

/** The ApiError for a failed response. A 401 on a signed-in call means the session is gone. */
async function failure(response: Response, session = true): Promise<ApiError> {
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
  if (response.status === 401 && session) {
    csrfToken = null
    onUnauthorized?.()
  }
  return new ApiError(response.status, code, message)
}

async function send(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(`${BASE_URL}${path}`, { credentials: BASE_URL ? 'include' : 'same-origin', ...init })
  } catch (error) {
    if (init.signal?.aborted) throw error
    throw new ApiError(0, 'network_error', 'Cannot reach the ComplyAgent API. Check that the backend is running.')
  }
}

/** Downloads a file the API sends (e.g. the audit trail as CSV) and saves it in the browser. */
async function download(path: string, fallbackName: string): Promise<void> {
  const response = await send(path, { method: 'GET' })
  if (!response.ok) throw await failure(response)
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/** One step the agents report while they work (the backend's server-sent events). */
export interface AgentStep {
  node: string
  actor: string
  summary: string
  round: number | null
}

/**
 * A POST whose answer is a stream of server-sent events: each `step` goes to `onStep` as the
 * agents finish it, and the final `done` event's data is the result.
 */
async function stream<T>(
  path: string,
  body: unknown,
  onStep: (step: AgentStep) => void,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'text/event-stream' }
  if (csrfToken) headers['X-CSRF-Token'] = csrfToken
  const response = await send(path, { method: 'POST', headers, body: JSON.stringify(body ?? {}), signal })
  if (!response.ok || !response.body) throw await failure(response)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      const kind = /^event: (.+)$/m.exec(block)?.[1] ?? 'message'
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data: '))
        .map((line) => line.slice(6))
        .join('\n')
      if (!data) continue
      const parsed = JSON.parse(data)
      if (kind === 'step') onStep(parsed as AgentStep)
      else if (kind === 'done') return parsed as T
      else if (kind === 'error') throw new ApiError(parsed.status ?? 500, parsed.code ?? 'http_error', parsed.message)
    }
  }
  throw new ApiError(0, 'network_error', 'The connection closed before the agents finished. Reload to see the result.')
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
  conversation: (customerId: number) =>
    request<Conversation | null>(`/api/admin/customers/${customerId}/conversation`),
  startConversation: (customerId: number, signal?: AbortSignal) =>
    request<Conversation>(`/api/admin/customers/${customerId}/conversation`, { method: 'POST', signal }),
  customerReplied: (id: number, body: { subject?: string; body: string }, signal?: AbortSignal) =>
    request<Conversation>(`/api/admin/conversations/${id}/inbound`, { method: 'POST', json: body, signal }),
  redraft: (id: number, feedback: string | null, signal?: AbortSignal) =>
    request<Conversation>(`/api/admin/conversations/${id}/redraft`, { method: 'POST', json: { feedback }, signal }),
  closeConversation: (id: number, note: string | null) =>
    request<Conversation>(`/api/admin/conversations/${id}/close`, { method: 'POST', json: { note } }),
  approveMessage: (id: number, body: { subject?: string; body?: string; note?: string }) =>
    request<Conversation>(`/api/admin/messages/${id}/approve`, { method: 'POST', json: body }),
  rejectMessage: (id: number, note: string) =>
    request<Conversation>(`/api/admin/messages/${id}/reject`, { method: 'POST', json: { note } }),
  sendMessage: (id: number) => request<Conversation>(`/api/admin/messages/${id}/send`, { method: 'POST' }),

  inbox: () => request<Inbox>('/api/admin/inbox'),
  collectionsSummary: () => request<CollectionsDashboard>('/api/admin/collections/summary'),
  pollEmail: () => request<MailboxPoll>('/api/admin/email/poll', { method: 'POST' }),
  ledger: (customerId: number) => request<Ledger>(`/api/admin/customers/${customerId}/ledger`),
  recordPayment: (customerId: number, body: { amount: number; paid_on: string; note?: string }) =>
    request<Ledger>(`/api/admin/customers/${customerId}/payments`, { method: 'POST', json: body }),
  recordPromise: (customerId: number, body: { due_on: string; amount?: number }) =>
    request<Ledger>(`/api/admin/customers/${customerId}/promises`, { method: 'POST', json: body }),
  recordContact: (
    customerId: number,
    body: { channel?: 'phone' | 'sms'; outcome: 'reached' | 'no_answer' | 'voicemail'; note?: string },
  ) => request<Ledger>(`/api/admin/customers/${customerId}/contacts`, { method: 'POST', json: body }),
  doNotContact: (customerId: number, reason: string) =>
    request<CustomerDetail>(`/api/admin/customers/${customerId}/do-not-contact`, { method: 'POST', json: { reason } }),
  liftDoNotContact: (customerId: number, note: string) =>
    request<CustomerDetail>(`/api/admin/customers/${customerId}/do-not-contact/lift`, {
      method: 'POST',
      json: { note },
    }),

  followUps: (status?: FollowUp['status']) =>
    request<FollowUp[]>(`/api/admin/follow-ups${status ? `?status=${status}` : ''}`),
  runDueFollowUps: () => request<FollowUpRun>('/api/admin/follow-ups/run-due', { method: 'POST' }),
  runFollowUp: (id: number) => request<Conversation>(`/api/admin/follow-ups/${id}/run`, { method: 'POST' }),

  exportAudit: (query: { customer_id?: number; event?: string; since?: string; until?: string } = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== '') params.set(key, String(value))
    const qs = params.toString()
    return download(`/api/admin/audit/export${qs ? `?${qs}` : ''}`, 'audit-trail.csv')
  },
  /** The same calls as below, streaming each agent step as it finishes. */
  startConversationLive: (customerId: number, onStep: (step: AgentStep) => void, signal?: AbortSignal) =>
    stream<Conversation>(`/api/admin/customers/${customerId}/conversation/stream`, {}, onStep, signal),
  customerRepliedLive: (
    id: number,
    body: { subject?: string; body: string },
    onStep: (step: AgentStep) => void,
    signal?: AbortSignal,
  ) => stream<Conversation>(`/api/admin/conversations/${id}/inbound/stream`, body, onStep, signal),
  redraftLive: (id: number, feedback: string | null, onStep: (step: AgentStep) => void, signal?: AbortSignal) =>
    stream<Conversation>(`/api/admin/conversations/${id}/redraft/stream`, { feedback }, onStep, signal),
  audit: (query: AuditQuery = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== '') params.set(key, String(value))
    const qs = params.toString()
    return request<AuditList>(`/api/admin/audit${qs ? `?${qs}` : ''}`)
  },
  agentGraph: () => request<{ mermaid: string }>('/api/admin/agents/graph'),

  counterfactuals: (customerId: number) => request<Simulation>(`/api/admin/customers/${customerId}/counterfactuals`),
  simulate: (customerId: number, body: SimulateRequest, signal?: AbortSignal) =>
    request<Simulation>(`/api/admin/customers/${customerId}/counterfactuals`, { method: 'POST', json: body, signal }),

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
