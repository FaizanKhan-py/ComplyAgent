import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { answer, config, customer, customerDetail, doc, health, mockApi, portfolio } from '../test/fixtures'
import { CustomerRecord } from './CustomerRecord'
import { AgentChat } from './AgentChat'
import { Customers } from './Customers'
import { Documents } from './Documents'
import { Playground } from './Playground'
import { PredictDataset } from './PredictDataset'
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
    expect(screen.getByText('python -m app.manage score-customers')).toBeInTheDocument()
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
    expect(within(row).getByText('zainab.khan7@example.net')).toBeInTheDocument()
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

  it('shows this month\'s overdue customers, riskiest first, each with a details button', async () => {
    const user = userEvent.setup()
    const base = 'GET /api/admin/customers?'
    const api = mockApi({
      [`${base}sort=id&order=asc&page=1&page_size=25`]: list([customer(), customer({ customer_id: 8, latest_status: -1 })]),
      [`${base}payment=overdue&sort=probability&order=desc&page=1&page_size=25`]: list([customer()], 6819),
    })
    page(<Customers />)

    await screen.findByText('Customer 8')
    await user.click(screen.getByRole('radio', { name: 'Overdue this month' }))
    await waitFor(() => expect(api.calls.at(-1)!.key).toContain('payment=overdue&sort=probability&order=desc'))
    await waitFor(() => expect(screen.queryByText('Customer 8')).not.toBeInTheDocument())
    expect(screen.getByRole('radio', { name: 'Overdue this month' })).toBeChecked()
    expect(screen.getByText(/late on their latest bill/)).toBeInTheDocument()
    expect(screen.getByLabelText('Sort by')).toHaveValue('risk')
    const row = screen.getByRole('link', { name: 'Customer 7' }).closest('tr')!
    expect(within(row).getByText('NT$3,913')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'View details of customer 7' })).toHaveAttribute('href', '/customers/7')
    expect(screen.getByText('Showing 1–1 of 6,819')).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'All customers' }))
    expect(await screen.findByText('Customer 8')).toBeInTheDocument()
    expect(api.calls.at(-1)!.key).toBe(`${base}sort=id&order=asc&page=1&page_size=25`)
  })

  it('says so when nobody is overdue this month', async () => {
    mockApi({ 'GET /api/admin/customers?payment=overdue&sort=probability&order=desc&page=1&page_size=25': list([]) })
    render(
      <MemoryRouter initialEntries={['/customers?payment=overdue&sort=probability&order=desc']}>
        <Customers />
      </MemoryRouter>,
    )
    expect(await screen.findByText('No customers are overdue this month')).toBeInTheDocument()
  })

  it('marks an overdue record, shows the message button and goes back to the same list', async () => {
    mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'GET /api/admin/customers/8': () => ({ body: { ...customerDetail, customer_id: 8, latest_status: 0 } }),
    })
    const view = render(
      <MemoryRouter initialEntries={[{ pathname: '/customers/7', state: { from: '/customers?payment=overdue' } }]}>
        <Routes>
          <Route path="/customers/:customerId" element={<CustomerRecord />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText(/overdue this month \(2 months late on the Sep 2005 bill\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate message' })).toBeEnabled()
    expect(screen.getByRole('link', { name: /Customer Records/ })).toHaveAttribute('href', '/customers?payment=overdue')
    view.unmount()

    record('/customers/8')
    expect(await screen.findByText(/not overdue this month/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Generate message' })).not.toBeInTheDocument()
  })

  const draft = {
    customer_id: 7,
    risk_level: 'high',
    status: 'draft',
    reason: null,
    decision: {
      action: 'send_reminder',
      channel: 'sms',
      priority: 'medium',
      reasons: [{ text: 'Two months late, Stage 2.', fact_ids: ['F1'], source_ids: [1] }],
      needs_human_review: false,
    },
    subject: 'ComplyAgent Finance: about your account',
    body: 'Dear customer,\n\nA reminder that NT$3,913 on your card is overdue. Reference CA-000007\nComplyAgent Finance',
    sources: [{ number: 1, document_id: 'm1', filename: 'messaging_policy.docx', page_number: null, score: 0.8, text: 'A collections SMS is at most 300 characters long.' }],
    checks: ['Names ComplyAgent Finance, the amount (NT$3,913) and the reference', '14 of 150 words; the subject has no amount'],
    corrected: ['the body must contain {reference}'],
    model: 'gpt-4.1-mini',
  }

  it('writes a message draft and shows the decision, checks and policy behind it', async () => {
    const user = userEvent.setup()
    let asked = 0
    mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'POST /api/admin/customers/7/message': () => {
        asked += 1
        return { body: draft }
      },
    })
    record('/customers/7')
    await user.click(await screen.findByRole('button', { name: 'Generate message' }))
    const card = within(await screen.findByRole('region', { name: 'Message draft' }))
    expect(await card.findByText(/A reminder that NT\$3,913 on your card is overdue/)).toBeInTheDocument()
    expect(card.getByText('ComplyAgent Finance: about your account')).toBeInTheDocument()
    expect(card.getByText('Send a reminder · Email · medium priority')).toBeInTheDocument()
    expect(card.getByText('High-risk tone')).toBeInTheDocument()
    expect(card.getByText('Two months late, Stage 2.')).toBeInTheDocument()
    expect(card.getByText('14 of 150 words; the subject has no amount')).toBeInTheDocument()
    expect(card.getByText(/Fixed on the second try: the body must contain \{reference\}/)).toBeInTheDocument()
    expect(card.getByText(/Policies it follows \(messaging_policy.docx\)/)).toBeInTheDocument()
    expect(card.getByText(/Draft: not sent yet/)).toBeInTheDocument()

    await user.click(card.getByRole('button', { name: 'Write again' }))
    await waitFor(() => expect(asked).toBe(2))
  })

  it('sends the email only after confirmation and says delivery is not connected yet', async () => {
    const user = userEvent.setup()
    mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'POST /api/admin/customers/7/message': () => ({ body: { ...draft } }),
    })
    record('/customers/7')
    await user.click(await screen.findByRole('button', { name: 'Generate message' }))
    const card = within(await screen.findByRole('region', { name: 'Message draft' }))
    await user.click(await card.findByRole('button', { name: 'Send email' }))

    expect(screen.getByText('Send this email?')).toBeInTheDocument()
    const dialogText = screen.getByText('Send this email?').closest('dialog')!.textContent
    expect(dialogText).toContain('To zainab.khan7@example.net (customer 7)')
    await user.click(screen.getByRole('button', { name: 'Cancel', hidden: true }))
    expect(card.queryByText(/Marked as sent/)).not.toBeInTheDocument()

    await user.click(card.getByRole('button', { name: 'Send email' }))
    const dialog = within(screen.getByText('Send this email?').closest('dialog')!)
    await user.click(dialog.getByRole('button', { name: 'Send email', hidden: true }))
    expect(await card.findByText(/Marked as sent to/)).toBeInTheDocument()
    expect(card.getByText(/Marked as sent to zainab.khan7@example.net on/)).toBeInTheDocument()
    expect(card.getByText(/nothing was actually emailed to zainab.khan7@example.net/)).toBeInTheDocument()
    expect(card.getByRole('button', { name: 'Sent' })).toBeDisabled()

    // A new draft has not been sent.
    await user.click(card.getByRole('button', { name: 'Write again' }))
    expect(await card.findByRole('button', { name: 'Send email' })).toBeEnabled()
    expect(card.queryByText(/Marked as sent/)).not.toBeInTheDocument()
  })

  it('cannot send to a customer with no email address on record', async () => {
    const user = userEvent.setup()
    mockApi({
      'GET /api/admin/customers/7': () => ({ body: { ...customerDetail, email: null } }),
      'POST /api/admin/customers/7/message': () => ({ body: draft }),
    })
    record('/customers/7')
    expect(await screen.findByText('Not on record')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Generate message' }))
    const card = within(await screen.findByRole('region', { name: 'Message draft' }))
    expect(await card.findByRole('button', { name: 'Send email' })).toBeDisabled()
    expect(card.getByText(/has no email address on record, so it cannot be sent/)).toBeInTheDocument()
  })

  it('explains when no message is written, and shows errors', async () => {
    const user = userEvent.setup()
    let fail = false
    mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'POST /api/admin/customers/7/message': () =>
        fail
          ? failure(503, 'llm_unreachable', 'Could not reach Azure.')()
          : {
              body: {
                ...draft,
                status: 'not_written',
                reason: 'The Decision Agent escalated this account to a person, so no automated message is written.',
                decision: { ...draft.decision, action: 'escalate_to_human', channel: 'phone' },
                body: null,
                checks: [],
                corrected: [],
                sources: [],
              },
            },
    })
    record('/customers/7')
    await user.click(await screen.findByRole('button', { name: 'Generate message' }))
    expect(await screen.findByText(/No email was written. The Decision Agent escalated/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Copy' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send email' })).not.toBeInTheDocument()

    fail = true
    await user.click(screen.getByRole('button', { name: 'Write again' }))
    expect(await screen.findByText(/Could not write the message: Could not reach Azure./)).toBeInTheDocument()
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
    expect(account.getByText('Email').nextSibling).toHaveTextContent('zainab.khan7@example.net')
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

  it('asks the policy assistant about the customer and shows cited answers', async () => {
    const user = userEvent.setup()
    const section = (key: string, title: string) => ({
      ...answer(),
      key,
      title,
      question: `${title}?`,
    })
    const api = mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'POST /api/admin/customers/7/guidance': () => ({
        body: {
          customer_id: 7,
          months_late: 2,
          days_overdue_estimate: 60,
          latest_bill: 3913,
          default_probability: 0.8231,
          risk_level: 'high',
          sections: [section('actions', 'Required actions'), { ...section('escalation', 'Escalation to a human'), ...answer({ answer_mode: 'no_evidence', insufficient_evidence: true, sources: [], answer: 'Not covered.' }) }],
        },
      }),
    })
    record('/customers/7')
    await user.click(await screen.findByRole('button', { name: 'Check the policies' }))
    const guidance = within(screen.getByRole('region', { name: 'Policy guidance' }))
    expect(await guidance.findByText('Required actions')).toBeInTheDocument()
    expect(guidance.getByText(/2 months late, treated as about 60 days overdue/)).toBeInTheDocument()
    expect(guidance.getByRole('link', { name: 'Source 1' })).toHaveAttribute('href', '#guidance-actions-source-1')
    expect(guidance.getByText('No relevant evidence')).toBeInTheDocument()
    expect(guidance.getByRole('button', { name: 'Check again' })).toBeEnabled()
    expect(api.count('POST /api/admin/customers/7/guidance')).toBe(1)
  })

  it('explains when an account is not overdue, and shows guidance errors', async () => {
    const user = userEvent.setup()
    let fail = false
    mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'POST /api/admin/customers/7/guidance': () =>
        fail
          ? failure(503, 'embeddings_unavailable', 'Embedding model could not be loaded.')()
          : { body: { customer_id: 7, months_late: 0, days_overdue_estimate: 0, latest_bill: 0, default_probability: 0.1, risk_level: 'low', sections: [] } },
    })
    record('/customers/7')
    await user.click(await screen.findByRole('button', { name: 'Check the policies' }))
    expect(await screen.findByText(/not overdue, so no collections policy applies/)).toBeInTheDocument()
    fail = true
    await user.click(screen.getByRole('button', { name: 'Check again' }))
    expect(await screen.findByText('Embedding model could not be loaded.')).toBeInTheDocument()
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
    const detail = 'gpt-4.1-mini (Azure OpenAI via LangChain): last request failed. Could not reach the model provider.'
    mockApi({
      'GET /api/admin/config': () => ({ body: { ...config, llm_provider: 'azure', llm_model: 'gpt-4.1-mini' } }),
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

describe('Collections Agent', () => {
  const reply = (answer: string, steps: { tool: string; input: string }[] = []) => ({
    answer,
    conversation_id: 'c1',
    steps,
    model: 'gpt-4.1-mini',
  })

  it('shows the answer and the tools used, and continues the same conversation', async () => {
    const user = userEvent.setup()
    let turn = 0
    const api = mockApi({
      'POST /api/admin/agent/chat': () =>
        ++turn === 1
          ? { body: reply('Customer 4 is 3 months late.', [{ tool: 'customer_overview', input: '{"customer_id": 4}' }]) }
          : { body: reply('Yes.') },
    })
    page(<AgentChat />)
    await user.type(screen.getByLabelText('Message'), 'How is customer 4?{Enter}')
    expect(await screen.findByText('Customer 4 is 3 months late.')).toBeInTheDocument()
    expect(screen.getByText('Looked up the customer')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Message'), 'Late?{Enter}')
    expect(await screen.findByText('Yes.')).toBeInTheDocument()
    expect(screen.getByText('No tools needed')).toBeInTheDocument()
    const sent = api.calls.filter((c) => c.key === 'POST /api/admin/agent/chat').map((c) => JSON.parse(String(c.init.body)))
    expect(sent).toEqual([
      { message: 'How is customer 4?', conversation_id: null },
      { message: 'Late?', conversation_id: 'c1' },
    ])

    // A new conversation forgets the id, so the server starts a fresh memory.
    await user.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(screen.getByText('Ask the Collections Agent')).toBeInTheDocument()
  })

  it('shows why the agent could not answer', async () => {
    const user = userEvent.setup()
    mockApi({
      'POST /api/admin/agent/chat': () => ({
        status: 503,
        body: { error: { code: 'llm_not_configured', message: 'The Collections Agent needs a language model.' } },
      }),
    })
    page(<AgentChat />)
    await user.click(screen.getByRole('button', { name: 'Which high-risk customers are overdue right now?' }))
    expect(await screen.findByText('The Collections Agent needs a language model.')).toBeInTheDocument()
  })
})

describe('Predict Dataset', () => {
  const prediction = (row: number, probability: number, actual: 0 | 1 | null) => ({
    row,
    id: row,
    limit_bal: 50000,
    age: 30,
    default_probability: probability,
    predicted_default: probability >= 0.5 ? 1 : 0,
    risk_level: probability >= 0.7 ? 'high' : probability >= 0.4 ? 'medium' : 'low',
    actual_default: actual,
  })
  const model = {
    available: true,
    message: null,
    model_name: 'XGBoost (tuned)',
    features: ['LIMIT_BAL', 'AGE'],
    threshold: 0.5,
    test_metrics: { roc_auc: 0.769, recall: 0.62, accuracy: 0.74 },
  }

  it('scores an uploaded dataset and shows the predicted target for every row', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const api = mockApi({
      'GET /api/admin/predict/model': () => ({ body: model }),
      'POST /api/admin/predict/dataset': () => ({
        body: {
          filename: 'customers.csv',
          model_name: 'XGBoost (tuned)',
          threshold: 0.5,
          rows: 3,
          predicted_defaults: 1,
          by_risk: { high: 1, medium: 0, low: 2 },
          scores: { accuracy: 2 / 3, precision: 1, recall: 0.5, f1: 0.67, tn: 1, fp: 0, fn: 1, tp: 1 },
          predictions: [prediction(1, 0.82, 1), prediction(2, 0.1, 0), prediction(3, 0.2, 1)],
          csv: 'ID,Y\n1,1\n',
        },
      }),
    })
    page(<PredictDataset />)
    expect(await screen.findByText('0.769', { exact: false })).toBeInTheDocument()
    const input = screen.getByLabelText('Choose a dataset to score')

    await user.upload(input, new File(['x'], 'notes.txt'))
    expect(await screen.findByText(/“notes.txt” is not a CSV or Excel file/)).toBeInTheDocument()
    expect(api.count('POST /api/admin/predict/dataset')).toBe(0)

    await user.upload(input, new File(['ID,X1'], 'customers.csv'))
    const summary = within(await screen.findByRole('region', { name: 'Prediction summary' }))
    expect(summary.getByText('Rows scored').nextSibling).toHaveTextContent('3')
    expect(summary.getByText('Accuracy vs. actual').nextSibling).toHaveTextContent('66.7%')
    expect(screen.getAllByText('0 · Will not default')).toHaveLength(2)
    expect(screen.getByText('1 · Will default')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download CSV with target column' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Predicted risk'), 'high')
    expect(screen.queryByText('0 · Will not default')).not.toBeInTheDocument()
    expect(screen.getByText('Showing 1–1 of 1')).toBeInTheDocument()
  })

  it('explains how to create the model when the notebook has not saved one', async () => {
    mockApi({
      'GET /api/admin/predict/model': () => ({
        body: { ...model, available: false, message: 'No trained model found. Run the notebook.' },
      }),
    })
    page(<PredictDataset />)
    expect(await screen.findByText('No trained model found. Run the notebook.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose file' })).toBeDisabled()
  })
})
