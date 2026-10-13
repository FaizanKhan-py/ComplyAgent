import type {
  AdminConfig,
  Health,
  KnowledgeBaseStats,
  PolicyDocument,
  QueryResponse,
} from '../api/types'

export const config: AdminConfig = {
  supported_extensions: ['.md', '.pdf', '.txt'],
  max_upload_mb: 20,
  chunk_size: 500,
  chunk_overlap: 80,
  embedding_model: 'fastembed:BAAI/bge-small-en-v1.5',
  llm_model: 'claude-opus-5-5',
  default_top_k: 5,
  default_min_score: 0.6,
}

export function doc(overrides: Partial<PolicyDocument> = {}): PolicyDocument {
  return {
    id: 'a1b2c3d4e5f60718',
    filename: 'hardship_policy.pdf',
    file_type: 'pdf',
    size_bytes: 20480,
    status: 'indexed',
    error: null,
    page_count: 3,
    char_count: 4200,
    chunk_count: 9,
    embedding_model: config.embedding_model,
    uploaded_at: '2026-10-13T09:00:00+00:00',
    updated_at: '2026-10-13T09:00:05+00:00',
    indexed_at: '2026-10-13T09:00:05+00:00',
    ...overrides,
  }
}

export function stats(overrides: Partial<KnowledgeBaseStats> = {}): KnowledgeBaseStats {
  return {
    status: 'ready',
    total_documents: 3,
    by_status: { pending: 0, processing: 0, indexed: 3, failed: 0 },
    by_file_type: { pdf: 2, txt: 1 },
    total_size_bytes: 51200,
    indexed_characters: 9000,
    total_chunks: 14,
    searchable_chunks: 14,
    stale_documents: 0,
    last_indexed_at: '2026-10-13T09:00:05+00:00',
    embedding_model: config.embedding_model,
    chunk_size: 500,
    chunk_overlap: 80,
    ...overrides,
  }
}

export const health: Health = {
  status: 'degraded',
  components: {
    database: { status: 'ok', detail: 'SQLite knowledge base reachable' },
    storage: { status: 'ok', detail: 'Upload storage is writable' },
    embeddings: { status: 'ok', detail: 'BAAI/bge-small-en-v1.5 loaded' },
    llm: { status: 'degraded', detail: 'Not configured: answers are retrieval-only' },
  },
}

export function answer(overrides: Partial<QueryResponse> = {}): QueryResponse {
  return {
    answer: 'A plan may cut the installment by up to 50 percent [1].',
    answer_mode: 'generated',
    insufficient_evidence: false,
    model: 'claude-opus-5-5',
    warning: null,
    sources: [
      {
        rank: 1,
        document_id: 'a1b2c3d4e5f60718',
        filename: 'hardship_policy.pdf',
        file_type: 'pdf',
        page_number: 2,
        chunk_index: 4,
        score: 0.8123,
        text: 'A hardship plan may reduce the monthly installment by up to 50 percent.',
        cited: true,
      },
      {
        rank: 2,
        document_id: 'ffeeddccbbaa0099',
        filename: 'contact_policy.txt',
        file_type: 'txt',
        page_number: null,
        chunk_index: 0,
        score: 0.6412,
        text: 'Customers may only be contacted between 8:00 a.m. and 9:00 p.m.',
        cited: false,
      },
    ],
    retrieval: { top_k: 5, min_score: 0.6, embedding_model: config.embedding_model, chunks_searched: 14 },
    ...overrides,
  }
}

type Handler = (init: RequestInit) => { status?: number; body: unknown } | Promise<never>

/**
 * Replaces fetch with a router keyed by "METHOD /path". Tests assert on the real HTTP contract
 * (method, URL, headers, body), so a client/backend mismatch shows up here.
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: { key: string; init: RequestInit }[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const key = `${init.method ?? 'GET'} ${String(input)}`
    calls.push({ key, init })
    const handler = routes[key]
    if (!handler) throw new Error(`Unexpected request: ${key}`)
    const result = await handler(init)
    return new Response(JSON.stringify(result.body), {
      status: result.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, count: (key: string) => calls.filter((c) => c.key === key).length }
}
