import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { answer, config, customer, customerDetail, doc, health, mockApi, portfolio, stats } from '../test/fixtures'
import { CustomerRecord } from './CustomerRecord'
import { Customers } from './Customers'
import { Documents } from './Documents'
import { KnowledgeBase } from './KnowledgeBase'
import { Playground } from './Playground'
import { RiskDashboard } from './RiskDashboard'

afterEach(() => vi.unstubAllGlobals())

const page = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>)
const failure = (status: number, code: string, message: string) => () => ({
  status,
  body: { error: { code, message } },
})

describe('Risk Dashboard', () => {
  it('shows the portfolio totals, risk distribution, delinquency trend and model scores', async () => {
    mockApi({ 'GET /api/admin/portfolio/summary': () => ({ body: portfolio() }) })
    page(<RiskDashboard />)

    const overview = await screen.findByRole('region', { name: 'Portfolio overview' })
    expect(within(overview).getByText('Customers').nextSibling).toHaveTextContent('200')
    expect(within(overview).getByText('Paying late').nextSibling).toHaveTextContent('22.7%')
    expect(within(overview).getByText('At least one month late in Sep 2005')).toBeInTheDocument()
    expect(within(overview).getByText('High risk').nextSibling).toHaveTextContent('35')
    expect(within(overview).getByRole('link', { name: /17.5% of customers/ })).toHaveAttribute(
      'href',
      '/customers?risk=high&sort=probability&order=desc',
    )
    expect(within(overview).getByText('Actual defaults').nextSibling).toHaveTextContent('44')

    const risk = within(screen.getByRole('region', { name: 'Customers by predicted risk' }))
    expect(risk.getByText('Medium risk').closest('li')).toHaveTextContent('55')
    const actual = within(screen.getByRole('region', { name: 'Actual default rate by predicted risk' }))
    expect(actual.getByText('High risk').closest('li')).toHaveTextContent('58%')
    const trend = within(screen.getByRole('region', { name: 'Delinquency trend' }))
    expect(trend.getByText('Aug 2005').closest('li')).toHaveTextContent('14.8%')
    const education = within(screen.getByRole('region', { name: 'Default rate by education' }))
    expect(education.getByText('High school').closest('li')).toHaveTextContent('25%')

    const model = within(screen.getByRole('region', { name: 'Prediction model' }))
    expect(model.getByText('XGBoost (tuned)')).toBeInTheDocument()
    expect(model.getByText('0.771')).toBeInTheDocument()
    expect(model.getByText(/62% of the customers who defaulted were flagged \(7 of 10\)/)).toBeInTheDocument()
    expect(model.getByText(/44% of the customers flagged did default \(7 of 12\)/)).toBeInTheDocument()
  })

  it('says how to train the model when customers are loaded but not scored', async () => {
    mockApi({ 'GET /api/admin/portfolio/summary': () => ({ body: portfolio({ model: null, by_risk: [] }) }) })
    page(<RiskDashboard />)
    expect(await screen.findByText('No risk predictions yet')).toBeInTheDocument()
    expect(screen.getByText('python -m app.manage train-model')).toBeInTheDocument()
    expect(screen.getByText('No model has been trained yet')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Delinquency trend' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Prediction model' })).not.toBeInTheDocument()
  })

  it('explains how to load the data when there is none, and shows errors', async () => {
    let fail = false
    mockApi({
      'GET /api/admin/portfolio/summary': () =>
        fail
          ? failure(500, 'internal_error', 'An unexpected server error occurred.')()
          : { body: portfolio({ loaded: false, customers: 0, model: null, by_risk: [], by_month: [], by_education: [] }) },
    })
    const first = page(<RiskDashboard />)
    expect(await screen.findByText('No customer data loaded yet')).toBeInTheDocument()
    expect(screen.getByText('python -m app.manage import-customers')).toBeInTheDocument()
    expect(screen.queryByText('Actual defaults')).not.toBeInTheDocument()

    first.unmount()
    fail = true
    page(<RiskDashboard />)
    expect(await screen.findByText('An unexpected server error occurred.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('Customer Records', () => {
  const list = (customers = [customer()], total = customers.length) => () => ({
    body: { customers, total, page: 1, page_size: 25 },
  })

  it('lists customers with their latest payment, predicted risk and actual status', async () => {
    mockApi({
      'GET /api/admin/customers?sort=id&order=asc&page=1&page_size=25': list([
        customer(),
        customer({ customer_id: 8, latest_status: -1, default_probability: null, predicted_default: null, risk_level: null, actual_default: false }),
      ]),
    })
    page(<Customers />)

    const row = (await screen.findByRole('link', { name: 'Customer 7' })).closest('tr')!
    expect(within(row).getByRole('link', { name: 'Customer 7' })).toHaveAttribute('href', '/customers/7')
    expect(within(row).getByText('Age 24 · University')).toBeInTheDocument()
    expect(within(row).getByText('NT$20,000')).toBeInTheDocument()
    expect(within(row).getByText('2 months late')).toBeInTheDocument()
    expect(within(row).getByText('High risk · 82%')).toBeInTheDocument()
    expect(within(row).getByText('Defaulted')).toBeInTheDocument()
    const other = screen.getByText('Customer 8').closest('tr')!
    expect(within(other).getByText('Paid in full')).toBeInTheDocument()
    expect(within(other).getByText('Not scored')).toBeInTheDocument()
    expect(within(other).getByText('Did not default')).toBeInTheDocument()
    expect(screen.getByText('Showing 1–2 of 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })

  it('sends the search, filters, sort and page to the API', async () => {
    const user = userEvent.setup()
    const base = 'GET /api/admin/customers?'
    const api = mockApi({
      [`${base}sort=id&order=asc&page=1&page_size=25`]: list([customer()], 60),
      [`${base}sort=id&order=asc&page=2&page_size=25`]: list([customer()], 60),
      [`${base}risk=high&sort=id&order=asc&page=1&page_size=25`]: list([customer()], 60),
      [`${base}risk=high&actual=paid&sort=probability&order=desc&page=1&page_size=25`]: list([], 0),
      [`${base}search=7&risk=high&actual=paid&sort=probability&order=desc&page=1&page_size=25`]: list(),
    })
    page(<Customers />)

    await user.click(await screen.findByRole('button', { name: 'Next' }))
    await waitFor(() => expect(api.calls.at(-1)!.key).toContain('page=2'))
    await screen.findByText('Customer 7')
    await user.selectOptions(screen.getByLabelText('Predicted risk'), 'High risk')
    await waitFor(() => expect(api.calls.at(-1)!.key).toContain('risk=high&sort=id&order=asc&page=1'))
    await user.selectOptions(screen.getByLabelText('Actual status'), 'Did not default')
    await user.selectOptions(screen.getByLabelText('Sort by'), 'Highest predicted risk')
    expect(await screen.findByText('No customers match')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Search'), '7')
    expect(await screen.findByText('Customer 7')).toBeInTheDocument()
    expect(api.calls.at(-1)!.key).toContain('search=7&risk=high&actual=paid&sort=probability')
  })

  it('opens with the filters in the address', async () => {
    mockApi({
      'GET /api/admin/customers?actual=defaulted&sort=id&order=asc&page=1&page_size=25': list([]),
    })
    render(
      <MemoryRouter initialEntries={['/customers?actual=defaulted']}>
        <Customers />
      </MemoryRouter>,
    )
    expect(await screen.findByText('No customers match')).toBeInTheDocument()
    expect(screen.getByLabelText('Actual status')).toHaveValue('defaulted')
  })

  const record = (path: string) =>
    render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/customers/:customerId" element={<CustomerRecord />} />
        </Routes>
      </MemoryRouter>,
    )

  it('shows one customer with account details, the prediction and payment history', async () => {
    mockApi({ 'GET /api/admin/customers/7': () => ({ body: customerDetail }) })
    record('/customers/7')
    expect(await screen.findByRole('heading', { name: 'Customer 7' })).toBeInTheDocument()
    const account = within(screen.getByRole('region', { name: 'Account details' }))
    expect(account.getByText('NT$20,000')).toBeInTheDocument()
    expect(account.getByText('Education').nextSibling).toHaveTextContent('University')
    const prediction = within(screen.getByRole('region', { name: 'Risk prediction' }))
    expect(prediction.getByText('High risk · 82%')).toBeInTheDocument()
    expect(prediction.getByText('Will default')).toBeInTheDocument()
    expect(prediction.getByText('Defaulted')).toBeInTheDocument()
    expect(prediction.getByText(/held back for testing/)).toBeInTheDocument()
    const history = within(screen.getByRole('region', { name: 'Payment history' }))
    expect(history.getByText(/Paid in full 50% of the months that count · average delay 0.7 months/)).toBeInTheDocument()
    const september = history.getByText('Sep 2005').closest('tr')!
    expect(within(september).getByText('2 months late')).toBeInTheDocument()
    expect(within(september).getByText('NT$3,913')).toBeInTheDocument()
    expect(within(history.getByText('Aug 2005').closest('tr')!).getByText('NT$689')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Customer Records/ })).toHaveAttribute('href', '/customers')
  })

  it('says when a customer has no prediction, or does not exist', async () => {
    mockApi({
      'GET /api/admin/customers/8': () => ({
        body: { ...customerDetail, customer_id: 8, default_probability: null, predicted_default: null, risk_level: null, data_split: null },
      }),
      'GET /api/admin/customers/999': failure(404, 'not_found', 'Customer not found.'),
    })
    const first = record('/customers/8')
    expect(await screen.findByText(/has not been scored/)).toBeInTheDocument()
    first.unmount()
    record('/customers/999')
    expect(await screen.findByText('Customer not found.')).toBeInTheDocument()
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
  const ACTIVITY = [
    { id: 2, document_id: 'x', filename: 'scan.pdf', event: 'failed', detail: 'No extractable text', created_at: '2026-10-13T09:01:00+00:00' },
    { id: 1, document_id: 'y', filename: 'policy.txt', event: 'indexed', detail: '4 chunks', created_at: '2026-10-13T09:00:00+00:00' },
  ]

  it('shows index statistics, configuration and health', async () => {
    mockApi({
      'GET /api/admin/kb/stats': () => ({ body: stats() }),
      'GET /api/admin/health': () => ({ body: health }),
      'GET /api/admin/activity?limit=8': () => ({ body: { events: ACTIVITY } }),
      'GET /api/admin/config': () => ({ body: config }),
    })
    page(<KnowledgeBase />)
    const section = await screen.findByRole('region', { name: 'Index statistics' })
    expect(within(section).getByText('3 / 3')).toBeInTheDocument()
    expect(within(section).getByText('14')).toBeInTheDocument()
    expect(screen.getByText('500 characters, 80 overlap')).toBeInTheDocument()
    expect(screen.getByText('2 PDF · 1 TXT')).toBeInTheDocument()
    expect(await screen.findByText('anthropic · claude-opus-5-5')).toBeInTheDocument()
    expect(await screen.findByText('Upload storage is writable')).toBeInTheDocument()
    expect(await screen.findByText('Ingestion failed')).toBeInTheDocument()
    expect(screen.getByText('No extractable text')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'All documents' })).toHaveAttribute('href', '/assistant/documents')
  })

  it('explains an empty knowledge base and disables rebuild', async () => {
    mockApi({
      'GET /api/admin/kb/stats': () => ({
        body: stats({ status: 'empty', total_documents: 0, total_chunks: 0, searchable_chunks: 0, by_file_type: {}, last_indexed_at: null, by_status: { pending: 0, processing: 0, indexed: 0, failed: 0 } }),
      }),
      'GET /api/admin/health': () => ({ body: health }),
      'GET /api/admin/activity?limit=8': () => ({ body: { events: ACTIVITY } }),
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
      'GET /api/admin/activity?limit=8': () => ({ body: { events: ACTIVITY } }),
      'GET /api/admin/config': () => ({ body: config }),
      'POST /api/admin/kb/rebuild': () => ({ status: 202, body: { queued: 3 } }),
    })
    page(<KnowledgeBase />)
    await user.click(await screen.findByRole('button', { name: 'Rebuild index' }))
    await user.click(screen.getAllByRole('button', { name: 'Rebuild index', hidden: true })[1])
    expect(await screen.findByText('Rebuild started for 3 documents.')).toBeInTheDocument()
    expect(api.count('POST /api/admin/kb/rebuild')).toBe(1)
  })

  it('names the answer model and its provider as the backend reports them', async () => {
    mockApi({
      'GET /api/admin/kb/stats': () => ({ body: stats() }),
      'GET /api/admin/health': () => ({ body: health }),
      'GET /api/admin/activity?limit=8': () => ({ body: { events: ACTIVITY } }),
      'GET /api/admin/config': () => ({ body: { ...config, llm_provider: 'ollama', llm_model: 'llama3.2' } }),
    })
    page(<KnowledgeBase />)
    expect(await screen.findByText('ollama · llama3.2')).toBeInTheDocument()
  })
})

describe('AI Assistant', () => {
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

  it('warns when the configured answer model is down, using the live health detail', async () => {
    const detail = 'Ollama is not reachable at http://127.0.0.1:11434. Is it running?'
    mockApi({
      'GET /api/admin/config': () => ({ body: { ...config, llm_provider: 'ollama', llm_model: 'llama3.2' } }),
      'GET /api/admin/health': () => ({
        body: { ...health, components: { ...health.components, llm: { status: 'error', detail } } },
      }),
    })
    page(<Playground />)
    expect(await screen.findByText(detail, { exact: false })).toBeInTheDocument()
    expect(screen.queryByText(/No answer model is configured/)).not.toBeInTheDocument()
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
