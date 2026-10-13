import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'
import { tokenStore } from '../api/client'
import { AuthProvider } from '../auth'
import { answer, config, doc, health, mockApi, stats } from '../test/fixtures'
import { Dashboard } from './Dashboard'
import { Documents } from './Documents'
import { KnowledgeBase } from './KnowledgeBase'
import { Playground } from './Playground'

afterEach(() => vi.unstubAllGlobals())

const page = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>)
const failure = (status: number, code: string, message: string) => () => ({
  status,
  body: { error: { code, message } },
})

describe('Dashboard', () => {
  it('shows real metrics, recent activity and service health', async () => {
    mockApi({
      'GET /api/admin/kb/stats': () => ({
        body: stats({ status: 'degraded', by_status: { pending: 0, processing: 0, indexed: 2, failed: 1 } }),
      }),
      'GET /api/admin/activity?limit=8': () => ({
        body: {
          events: [
            { id: 2, document_id: 'x', filename: 'scan.pdf', event: 'failed', detail: 'No extractable text', created_at: '2026-10-13T09:01:00+00:00' },
            { id: 1, document_id: 'y', filename: 'policy.txt', event: 'indexed', detail: '4 chunks', created_at: '2026-10-13T09:00:00+00:00' },
          ],
        },
      }),
      'GET /api/admin/health': () => ({ body: health }),
    })
    page(<Dashboard />)

    const overview = await screen.findByRole('region', { name: 'Overview' })
    expect(within(overview).getByText('Total documents').nextSibling).toHaveTextContent('3')
    expect(within(overview).getByText('Indexed').nextSibling).toHaveTextContent('2')
    expect(within(overview).getByText('Failed').nextSibling).toHaveTextContent('1')
    expect(within(overview).getByText('Degraded')).toBeInTheDocument()
    expect(within(overview).getByText('14 searchable chunks')).toBeInTheDocument()

    expect(await screen.findByText('Ingestion failed')).toBeInTheDocument()
    expect(screen.getByText('No extractable text')).toBeInTheDocument()
    expect(await screen.findByText('Answer model (LLM)')).toBeInTheDocument()
  })

  it('shows an empty state for a new knowledge base and an error state when a call fails', async () => {
    mockApi({
      'GET /api/admin/kb/stats': failure(500, 'internal_error', 'An unexpected server error occurred.'),
      'GET /api/admin/activity?limit=8': () => ({ body: { events: [] } }),
      'GET /api/admin/health': () => ({ body: health }),
    })
    page(<Dashboard />)
    expect(await screen.findByText('An unexpected server error occurred.')).toBeInTheDocument()
    expect(await screen.findByText('No activity yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('Documents', () => {
  it('lists documents with status, metadata and failure reasons', async () => {
    mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'GET /api/admin/documents': () => ({
        body: {
          documents: [
            doc(),
            doc({ id: 'bad', filename: 'scan.pdf', status: 'failed', error: 'No extractable text was found.', chunk_count: 0, page_count: null }),
          ],
        },
      }),
    })
    page(<Documents />)

    const row = (await screen.findByText('hardship_policy.pdf')).closest('tr')!
    expect(within(row).getByText('PDF')).toBeInTheDocument()
    expect(within(row).getByText('20.0 KB')).toBeInTheDocument()
    expect(within(row).getByText('Indexed')).toBeInTheDocument()
    expect(within(row).getByText('9')).toBeInTheDocument()

    const failed = screen.getByText('scan.pdf').closest('tr')!
    expect(within(failed).getByText('Failed')).toBeInTheDocument()
    expect(within(failed).getByText('No extractable text was found.')).toBeInTheDocument()
    expect(within(failed).getByRole('button', { name: 'Reindex scan.pdf' })).toHaveTextContent('Retry')
  })

  it('shows the empty state', async () => {
    mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'GET /api/admin/documents': () => ({ body: { documents: [] } }),
    })
    page(<Documents />)
    expect(await screen.findByText('No documents yet')).toBeInTheDocument()
  })

  it('uploads a file, rejects bad ones locally and surfaces server errors', async () => {
    const user = userEvent.setup({ applyAccept: false })
    let uploads = 0
    const api = mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'GET /api/admin/documents': () => ({ body: { documents: uploads ? [doc({ status: 'indexed' })] : [] } }),
      'POST /api/admin/documents': () => {
        uploads += 1
        return uploads === 1
          ? { status: 202, body: doc({ status: 'pending' }) }
          : failure(409, 'duplicate_document', "This file was already uploaded as 'hardship_policy.pdf'.")()
      },
    })
    page(<Documents />)
    const input = await screen.findByLabelText('Choose policy documents to upload')
    await screen.findByText('No documents yet')

    await user.upload(input, new File(['%PDF-1.4'], 'hardship_policy.pdf'))
    expect(await screen.findByText('Accepted. Indexing started.')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Delete hardship_policy.pdf' })).toBeInTheDocument()

    await user.upload(input, new File(['MZ'], 'tool.exe'))
    expect(await screen.findByText(/Unsupported file type/)).toBeInTheDocument()
    expect(api.count('POST /api/admin/documents')).toBe(1) // never sent to the server

    await user.upload(input, new File(['%PDF-1.4'], 'copy.pdf'))
    expect(await screen.findByText(/already uploaded as 'hardship_policy.pdf'/)).toBeInTheDocument()
  })

  it('deletes only after confirmation and reports the removed chunks', async () => {
    const user = userEvent.setup()
    let deleted = false
    const api = mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'GET /api/admin/documents': () => ({ body: { documents: deleted ? [] : [doc()] } }),
      'DELETE /api/admin/documents/a1b2c3d4e5f60718': () => {
        deleted = true
        return { body: { id: 'a1b2c3d4e5f60718', deleted: true, chunks_removed: 9 } }
      },
    })
    page(<Documents />)

    await user.click(await screen.findByRole('button', { name: 'Delete hardship_policy.pdf' }))
    expect(screen.getByText('Delete this document?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel', hidden: true }))
    expect(api.count('DELETE /api/admin/documents/a1b2c3d4e5f60718')).toBe(0)

    await user.click(screen.getByRole('button', { name: 'Delete hardship_policy.pdf' }))
    await user.click(screen.getByRole('button', { name: 'Delete document', hidden: true }))
    expect(await screen.findByText(/removed 9 indexed chunks/)).toBeInTheDocument()
    expect(await screen.findByText('No documents yet')).toBeInTheDocument()
  })

  it('keeps polling while a document is processing and disables its actions', async () => {
    let polls = 0
    mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'GET /api/admin/documents': () => {
        polls += 1
        return { body: { documents: [doc({ status: polls < 2 ? 'processing' : 'indexed' })] } }
      },
    })
    page(<Documents />)
    expect(await screen.findByText('Processing')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete hardship_policy.pdf' })).toBeDisabled()
    expect(await screen.findByText('Indexed', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete hardship_policy.pdf' })).toBeEnabled()
  })
})

describe('Knowledge Base', () => {
  it('shows index statistics, configuration and health', async () => {
    mockApi({
      'GET /api/admin/kb/stats': () => ({ body: stats() }),
      'GET /api/admin/health': () => ({ body: health }),
      'GET /api/admin/config': () => ({ body: config }),
    })
    page(<KnowledgeBase />)
    const section = await screen.findByRole('region', { name: 'Index statistics' })
    expect(within(section).getByText('3 / 3')).toBeInTheDocument()
    expect(within(section).getByText('14')).toBeInTheDocument()
    expect(screen.getByText('500 characters, 80 overlap')).toBeInTheDocument()
    expect(screen.getByText('2 PDF · 1 TXT')).toBeInTheDocument()
    expect(await screen.findByText('claude-opus-5-5')).toBeInTheDocument()
    expect(await screen.findByText('Upload storage is writable')).toBeInTheDocument()
  })

  it('explains an empty knowledge base and disables rebuild', async () => {
    mockApi({
      'GET /api/admin/kb/stats': () => ({
        body: stats({ status: 'empty', total_documents: 0, total_chunks: 0, searchable_chunks: 0, by_file_type: {}, last_indexed_at: null, by_status: { pending: 0, processing: 0, indexed: 0, failed: 0 } }),
      }),
      'GET /api/admin/health': () => ({ body: health }),
      'GET /api/admin/config': () => ({ body: { ...config, llm_model: null } }),
    })
    page(<KnowledgeBase />)
    expect(await screen.findByText(/The knowledge base is empty/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rebuild index' })).toBeDisabled()
    expect(await screen.findByText('Not configured (retrieval-only)')).toBeInTheDocument()
  })

  it('rebuilds after confirmation', async () => {
    const user = userEvent.setup()
    const api = mockApi({
      'GET /api/admin/kb/stats': () => ({ body: stats() }),
      'GET /api/admin/health': () => ({ body: health }),
      'GET /api/admin/config': () => ({ body: config }),
      'POST /api/admin/kb/rebuild': () => ({ status: 202, body: { queued: 3 } }),
    })
    page(<KnowledgeBase />)
    await user.click(await screen.findByRole('button', { name: 'Rebuild index' }))
    await user.click(screen.getAllByRole('button', { name: 'Rebuild index', hidden: true })[1])
    expect(await screen.findByText('Rebuild started for 3 documents.')).toBeInTheDocument()
    expect(api.count('POST /api/admin/kb/rebuild')).toBe(1)
  })
})

describe('RAG Playground', () => {
  it('shows the answer with only the sources the backend returned', async () => {
    const user = userEvent.setup()
    const api = mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'POST /api/admin/rag/query': () => ({ body: answer() }),
    })
    page(<Playground />)

    await user.type(screen.getByLabelText('Question'), 'How much can a plan reduce the installment?{Enter}')
    expect(await screen.findByText('Generated answer')).toBeInTheDocument()
    expect(screen.getByText(/up to 50 percent/, { selector: 'p.answer-text span' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Source 1' })).toHaveAttribute('href', '#turn-1-source-1')

    const sources = screen.getAllByRole('listitem').filter((li) => li.className === 'source')
    expect(sources).toHaveLength(2)
    expect(within(sources[0]).getByText('hardship_policy.pdf')).toBeInTheDocument()
    expect(within(sources[0]).getByText('Page 2 · Chunk 5')).toBeInTheDocument()
    expect(within(sources[0]).getByText('0.812')).toBeInTheDocument()
    expect(within(sources[0]).getByText('Cited')).toBeInTheDocument()
    // No page number is invented for a text file, and an uncited source is not marked cited.
    expect(within(sources[1]).getByText('Chunk 1')).toBeInTheDocument()
    expect(within(sources[1]).queryByText(/Page/)).not.toBeInTheDocument()
    expect(within(sources[1]).queryByText('Cited')).not.toBeInTheDocument()

    expect(JSON.parse(api.calls.at(-1)!.init.body as string)).toEqual({
      question: 'How much can a plan reduce the installment?',
      history: [],
    })
  })

  it('sends generated turns as history and clears them on a new conversation', async () => {
    const user = userEvent.setup()
    const api = mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'POST /api/admin/rag/query': () => ({ body: answer() }),
    })
    page(<Playground />)
    const box = screen.getByLabelText('Question')
    const lastBody = () => JSON.parse(api.calls.at(-1)!.init.body as string)

    await user.type(box, 'First question{Enter}')
    await screen.findByText('Generated answer')
    await user.type(box, 'Follow up{Enter}')
    await waitFor(() => expect(api.count('POST /api/admin/rag/query')).toBe(2))
    expect(lastBody().history).toEqual([
      { role: 'user', content: 'First question' },
      { role: 'assistant', content: answer().answer },
    ])

    await waitFor(() => expect(screen.getAllByText('Generated answer')).toHaveLength(2))
    await user.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(screen.queryByText('First question')).not.toBeInTheDocument()
    await user.type(box, 'Fresh start{Enter}')
    await waitFor(() => expect(api.count('POST /api/admin/rag/query')).toBe(3))
    expect(lastBody().history).toEqual([])
  })

  it('shows the no-evidence and retrieval-only outcomes honestly', async () => {
    const user = userEvent.setup()
    const replies = [
      answer({ answer: "I couldn't find anything in the indexed policy documents that answers this question.", answer_mode: 'no_evidence', insufficient_evidence: true, sources: [], model: null }),
      answer({ answer: 'No generated answer is available.', answer_mode: 'retrieval_only', model: null, warning: 'Answer generation failed: The LLM provider rate limit was hit.', sources: answer().sources.map((s) => ({ ...s, cited: false })) }),
    ]
    mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'POST /api/admin/rag/query': () => ({ body: replies.shift() }),
    })
    page(<Playground />)
    const box = screen.getByLabelText('Question')

    await user.type(box, 'Sourdough recipe?{Enter}')
    expect(await screen.findByText('No relevant evidence')).toBeInTheDocument()
    expect(screen.queryByText(/Retrieved sources/)).not.toBeInTheDocument()

    await user.type(box, 'Contact hours?{Enter}')
    expect(await screen.findByText(/Retrieval only/)).toBeInTheDocument()
    expect(screen.getByText(/rate limit was hit/)).toBeInTheDocument()
    expect(screen.queryByText('Cited')).not.toBeInTheDocument()
  })

  it('shows request failures and lets the user retry', async () => {
    const user = userEvent.setup()
    let attempt = 0
    mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'POST /api/admin/rag/query': () =>
        ++attempt === 1
          ? failure(503, 'embeddings_unavailable', 'Embedding model could not be loaded.')()
          : { body: answer() },
    })
    page(<Playground />)
    await user.type(screen.getByLabelText('Question'), 'Contact hours?{Enter}')
    expect(await screen.findByText('Embedding model could not be loaded.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Generated answer')).toBeInTheDocument()
  })
})

describe('Sign-in', () => {
  const app = () =>
    render(
      <MemoryRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    )
  const dashboardRoutes = {
    'GET /api/admin/kb/stats': () => ({ body: stats() }),
    'GET /api/admin/activity?limit=8': () => ({ body: { events: [] } }),
    'GET /api/admin/health': () => ({ body: health }),
  }

  it('requires the admin token, rejects a wrong one and signs in with the right one', async () => {
    const user = userEvent.setup()
    const api = mockApi({
      'GET /api/auth/status': () => ({ body: { auth_required: true } }),
      'GET /api/admin/session': (init) =>
        (init.headers as Record<string, string>).Authorization === 'Bearer right'
          ? { body: { ok: true } }
          : failure(401, 'unauthorized', 'A valid admin token is required.')(),
      ...dashboardRoutes,
    })
    app()

    const field = await screen.findByLabelText('Admin token')
    expect(api.count('GET /api/admin/kb/stats')).toBe(0) // nothing is fetched before sign-in
    await user.type(field, 'wrong{Enter}')
    expect(await screen.findByText('That token was not accepted.')).toBeInTheDocument()
    expect(tokenStore.get()).toBeNull()

    await user.clear(field)
    await user.type(field, 'right{Enter}')
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    expect(tokenStore.get()).toBe('right')
    for (const link of ['Dashboard', 'Documents', 'Knowledge Base', 'RAG Playground']) {
      expect(screen.getByRole('link', { name: link })).toBeInTheDocument()
    }

    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByLabelText('Admin token')).toBeInTheDocument()
    expect(tokenStore.get()).toBeNull()
  })

  it('goes straight in when the server does not require a token, and reports an unreachable API', async () => {
    mockApi({ 'GET /api/auth/status': () => ({ body: { auth_required: false } }), ...dashboardRoutes })
    const first = app()
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByText('Open access (dev mode)')).toBeInTheDocument()
    first.unmount()

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    app()
    expect(await screen.findByText(/Cannot reach the ComplyAgent API/)).toBeInTheDocument()
  })
})
