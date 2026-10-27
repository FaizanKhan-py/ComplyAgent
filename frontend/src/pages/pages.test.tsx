import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { answer, config, customer, customerDetail, doc, health, mockApi, portfolio } from '../test/fixtures'
import { AuditTrail } from './AuditTrail'
import { CustomerRecord } from './CustomerRecord'
import { FollowUps } from './FollowUps'
import { Inbox } from './Inbox'
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

  it('marks an overdue record, offers to start a conversation and goes back to the same list', async () => {
    mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'GET /api/admin/customers/7/conversation': () => ({ body: null }),
      'GET /api/admin/customers/8': () => ({ body: { ...customerDetail, customer_id: 8, latest_status: 0 } }),
      'GET /api/admin/customers/8/conversation': () => ({ body: null }),
    })
    const view = render(
      <MemoryRouter initialEntries={[{ pathname: '/customers/7', state: { from: '/customers?payment=overdue' } }]}>
        <Routes>
          <Route path="/customers/:customerId" element={<CustomerRecord />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText(/overdue this month \(2 months late on the Sep 2005 bill\)/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Start conversation' })).toBeEnabled()
    expect(screen.getByRole('link', { name: /Customer Records/ })).toHaveAttribute('href', '/customers?payment=overdue')
    view.unmount()

    record('/customers/8')
    expect(await screen.findByText(/Nothing is overdue, so there is nothing to collect/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start conversation' })).not.toBeInTheDocument()
  })

  const decision = {
    action: 'send_reminder',
    channel: 'email',
    priority: 'medium',
    reasons: [{ text: 'Two months late, Stage 2.', fact_ids: ['F1'], source_ids: [1] }],
    needs_human_review: false,
  }
  const outbound = (overrides = {}) => ({
    id: 1,
    direction: 'outbound',
    status: 'pending_approval',
    subject: 'ComplyAgent Finance: about your account',
    body: 'Dear customer,\n\nA reminder that NT$3,913 on your card is overdue. Reference CA-000007\nComplyAgent Finance',
    intent: null,
    intent_summary: null,
    review: {
      rounds: [
        {
          round: 1,
          checked_by: 'red_team',
          passed: false,
          issues: [],
          dropped: 1,
          decision_revised: null,
          findings: [
            { target: 'message', severity: 'blocker', issue: 'Too abrupt.', quote: 'Pay now', source_ids: [1], fix: 'Be polite.' },
          ],
        },
        { round: 2, checked_by: 'red_team', passed: true, issues: [], findings: [], dropped: 0, decision_revised: null },
      ],
      checks: ['14 of 150 words; the subject has no amount'],
      sources: [],
      decision,
    },
    edited: false,
    reviewed_by: null,
    reviewed_at: null,
    review_note: null,
    sent_by: null,
    sent_at: null,
    created_at: '2026-10-27T10:00:00+00:00',
    ...overrides,
  })
  const conversation = (messages: unknown[] = [outbound()], overrides = {}) => ({
    id: 3,
    customer_id: 7,
    status: 'open',
    status_reason: null,
    case_file: {
      analysed_by: 'model',
      model: 'gpt-4.1-mini',
      facts: [{ id: 'F1', text: 'The account is 2 months late.' }],
      analysis: {
        summary: 'Two months late and paying part of each bill.',
        situation: 'partial_payer',
        urgency: 'high',
        patterns: [{ text: 'Pays part of each bill.', fact_ids: ['F7'] }],
        care_flags: [{ text: 'Payments are shrinking.', fact_ids: ['F8'] }],
        open_questions: ['Can the customer pay the full amount?'],
      },
    },
    decision,
    created_by: 'officer@example.com',
    created_at: '2026-10-27T10:00:00+00:00',
    updated_at: '2026-10-27T10:00:00+00:00',
    messages,
    follow_ups: [] as unknown[],
    ...overrides,
  })
  const bodyOf = (api: ReturnType<typeof mockApi>, suffix: string) =>
    JSON.parse(String(api.calls.find((c) => c.key.endsWith(suffix))!.init.body))

  it('runs the agents, shows the case file and review, and sends only after approval', async () => {
    const user = userEvent.setup()
    const approved = outbound({
      status: 'approved',
      reviewed_by: 'officer@example.com',
      reviewed_at: '2026-10-27T10:05:00+00:00',
    })
    const api = mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'GET /api/admin/customers/7/conversation': () => ({ body: null }),
      'POST /api/admin/customers/7/conversation': () => ({ body: conversation() }),
      'POST /api/admin/messages/1/approve': () => ({ body: conversation([approved]) }),
      'POST /api/admin/messages/1/send': () => ({
        body: conversation([
          { ...approved, status: 'sent', sent_by: 'officer@example.com', sent_at: '2026-10-27T10:06:00+00:00' },
        ]),
      }),
    })
    record('/customers/7')
    await user.click(await screen.findByRole('button', { name: 'Start conversation' }))
    const card = within(await screen.findByRole('region', { name: 'Collections conversation' }))
    expect(await card.findByText('Two months late and paying part of each bill.')).toBeInTheDocument()
    expect(card.getByText('Pays part of each bill')).toBeInTheDocument() // the situation
    expect(card.getByText('Payments are shrinking.')).toBeInTheDocument()
    expect(card.getByText(/Decision: Send a reminder · medium priority/)).toBeInTheDocument()
    expect(card.getByText('Waiting for approval')).toBeInTheDocument()
    expect(card.getByText(/Compliance review: 2 rounds/)).toBeInTheDocument()
    expect(card.getByText(/“Pay now”: Too abrupt./)).toBeInTheDocument()
    expect(card.getByText(/1 finding dropped/)).toBeInTheDocument()
    expect(card.queryByRole('button', { name: 'Send email' })).not.toBeInTheDocument()

    await user.click(card.getByRole('button', { name: 'Approve' }))
    expect(await card.findByText('Approved, ready to send')).toBeInTheDocument()
    expect(bodyOf(api, '/approve')).toEqual({})

    await user.click(card.getByRole('button', { name: 'Send email' }))
    const dialog = within(screen.getByText('Send this email?').closest('dialog')!)
    expect(dialog.getByText('zainab.khan7@example.net')).toBeInTheDocument()
    await user.click(dialog.getByRole('button', { name: 'Send email', hidden: true }))
    expect(await card.findByText(/Sent by officer@example.com on/)).toBeInTheDocument()
    expect(card.getByText(/nothing was emailed/)).toBeInTheDocument()
    expect(api.count('POST /api/admin/messages/1/send')).toBe(1)
  })

  it("needs a reason to reject, and writes again with the officer's feedback", async () => {
    const user = userEvent.setup()
    const api = mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'GET /api/admin/customers/7/conversation': () => ({ body: conversation() }),
      'POST /api/admin/messages/1/reject': () => ({
        body: conversation([
          outbound({ status: 'rejected', reviewed_by: 'officer@example.com', review_note: 'Too formal.' }),
        ]),
      }),
      'POST /api/admin/conversations/3/redraft': () => ({ body: conversation([outbound({ id: 2 })]) }),
    })
    record('/customers/7')
    const card = within(await screen.findByRole('region', { name: 'Collections conversation' }))
    expect(await card.findByRole('button', { name: 'Reject' })).toBeDisabled()
    await user.type(card.getByLabelText(/Note or feedback/), 'Too formal.')
    await user.click(card.getByRole('button', { name: 'Reject' }))
    expect(await card.findByText(/Draft rejected by officer@example.com: “Too formal.”/)).toBeInTheDocument()
    expect(bodyOf(api, '/reject')).toEqual({ note: 'Too formal.' })
  })

  it('writes again with the feedback in the note', async () => {
    const user = userEvent.setup()
    const api = mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'GET /api/admin/customers/7/conversation': () => ({ body: conversation() }),
      'POST /api/admin/conversations/3/redraft': () => ({
        body: conversation([outbound({ status: 'superseded' }), outbound({ id: 2, body: 'A warmer email.' })]),
      }),
    })
    record('/customers/7')
    const card = within(await screen.findByRole('region', { name: 'Collections conversation' }))
    await user.type(await card.findByLabelText(/Note or feedback/), 'Be warmer.')
    await user.click(card.getByRole('button', { name: 'Write again with this feedback' }))
    expect(await card.findByText('A warmer email.')).toBeInTheDocument()
    expect(bodyOf(api, '/redraft')).toEqual({ feedback: 'Be warmer.' })
  })

  it("takes the customer's reply, shows its intent and the drafted answer, or a hand-over", async () => {
    const user = userEvent.setup()
    const sent = outbound({
      status: 'sent',
      sent_by: 'officer@example.com',
      sent_at: '2026-10-27T10:06:00+00:00',
      reviewed_by: 'officer@example.com',
    })
    const inbound = {
      ...outbound({ id: 2, review: null }),
      direction: 'inbound',
      status: 'received',
      subject: null,
      body: 'I lost my job. Can I pay less?',
      intent: 'hardship',
      intent_summary: 'The customer lost their job and asks to pay less.',
    }
    let escalate = false
    const api = mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'GET /api/admin/customers/7/conversation': () => ({ body: conversation([sent]) }),
      'POST /api/admin/conversations/3/inbound': () =>
        escalate
          ? {
              body: conversation([sent, inbound], {
                status: 'escalated',
                status_reason: 'The customer disputes the debt.',
              }),
            }
          : { body: conversation([sent, inbound, outbound({ id: 4, body: 'Dear customer, we can help.' })]) },
    })
    record('/customers/7')
    const card = within(await screen.findByRole('region', { name: 'Collections conversation' }))
    await user.type(await card.findByLabelText('Their email'), 'I lost my job. Can I pay less?')
    await user.click(card.getByRole('button', { name: "Add the customer's reply" }))
    expect(await card.findByText('Hardship')).toBeInTheDocument()
    expect(card.getByText('The customer lost their job and asks to pay less.')).toBeInTheDocument()
    expect(card.getByText('Dear customer, we can help.')).toBeInTheDocument()
    expect(bodyOf(api, '/inbound')).toEqual({ body: 'I lost my job. Can I pay less?' })

    escalate = true
    await user.type(card.getByLabelText('Their email'), 'This is not my debt.')
    await user.click(card.getByRole('button', { name: "Add the customer's reply" }))
    expect(await card.findByText('The customer disputes the debt.')).toBeInTheDocument()
    expect(card.getByText('With a person')).toBeInTheDocument()
  })


  it('shows the Follow-up Agent plan and runs it on request', async () => {
    const user = userEvent.setup()
    const sent = outbound({ status: 'sent', sent_by: 'officer@example.com', sent_at: '2026-10-27T10:06:00+00:00' })
    const scheduled = {
      id: 9,
      conversation_id: 3,
      customer_id: 7,
      due_at: '2026-10-30T10:06:00+00:00',
      action: 'send_reminder',
      reason: 'The contact policy allows a reminder after three days.',
      status: 'scheduled',
      outcome: null,
      created_at: '2026-10-27T10:06:00+00:00',
      done_at: null,
    }
    const api = mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'GET /api/admin/customers/7/conversation': () => ({
        body: conversation([sent], { follow_ups: [scheduled] }),
      }),
      'POST /api/admin/follow-ups/9/run': () => ({
        body: conversation([sent, outbound({ id: 5, body: 'A gentle reminder.' })], {
          follow_ups: [
            { ...scheduled, status: 'done', outcome: 'A follow-up email was drafted.', done_at: '2026-10-27T11:00:00+00:00' },
          ],
        }),
      }),
    })
    record('/customers/7')
    const card = within(await screen.findByRole('region', { name: 'Collections conversation' }))
    expect(await card.findByText(/if there is no reply by/)).toBeInTheDocument()
    expect(card.getByText(/send a reminder/)).toBeInTheDocument()
    await user.click(card.getByRole('button', { name: 'Run now' }))
    expect(await card.findByText('A gentle reminder.')).toBeInTheDocument()
    expect(card.getByText(/A follow-up email was drafted./)).toBeInTheDocument()
    expect(api.count('POST /api/admin/follow-ups/9/run')).toBe(1)
  })

  it('runs the what-ifs, flags a fairness gap and asks the Decision Agent', async () => {
    const user = userEvent.setup()
    const scenario = (key: string, label: string, kind: string, probability: number, change: number) => ({
      key,
      label,
      kind,
      probability,
      change,
      risk_level: probability >= 0.7 ? 'high' : probability >= 0.4 ? 'medium' : 'low',
      predicted_default: probability >= 0.5,
    })
    const simulation = {
      customer_id: 7,
      model_name: 'XGBoost (tuned)',
      threshold: 0.5,
      base: { probability: 0.62, risk_level: 'medium', predicted_default: true },
      scenarios: [
        scenario('catches_up', 'Catches up on every month', 'behaviour', 0.21, -0.41),
        scenario('sex', 'Same record, sex male', 'fairness', 0.7, 0.08),
      ],
      custom: null,
    }
    const api = mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'GET /api/admin/customers/7/conversation': () => ({ body: null }),
      'GET /api/admin/customers/7/counterfactuals': () => ({ body: simulation }),
      'POST /api/admin/customers/7/counterfactuals': () => ({
        body: {
          ...simulation,
          custom: {
            probability: 0.81,
            risk_level: 'high',
            predicted_default: true,
            change: 0.19,
            changes: { repayment_status: 4 },
            features: {},
            decision_error: null,
            recommendation: {
              ...decision,
              action: 'escalate_to_human',
              reasons: [{ text: 'Four months late.', fact_ids: ['F1'], source_ids: [] }],
            },
          },
        },
      }),
    })
    record('/customers/7')
    const card = within(await screen.findByRole('region', { name: 'Counterfactual simulator' }))
    await user.click(card.getByRole('button', { name: 'Run what-ifs' }))
    expect(await card.findByText('Catches up on every month')).toBeInTheDocument()
    expect(card.getByText('-41.0 points')).toBeInTheDocument()
    expect(card.getByText(/Changing only a personal attribute moves the prediction by up to 8%/)).toBeInTheDocument()

    await user.selectOptions(card.getByLabelText('Payment status'), '4')
    await user.click(card.getByLabelText(/Also ask the Decision Agent/))
    await user.click(card.getByRole('button', { name: 'Simulate' }))
    expect(await card.findByText('Escalate to a collections officer')).toBeInTheDocument()
    expect(card.getByText('Four months late.')).toBeInTheDocument()
    const sent = JSON.parse(String(api.calls.find((c) => c.key.startsWith('POST'))!.init.body))
    expect(sent).toMatchObject({ repayment_status: 4, decide: true })
  })

  it('records a request to stop contact, blocks new emails, and lifts it with a reason', async () => {
    const user = userEvent.setup()
    let flagged = false
    const flag = { reason: 'Asked by phone to stop.', set_by: 'officer@example.com', set_at: '2026-10-27T10:00:00+00:00' }
    const api = mockApi({
      'GET /api/admin/customers/7': () => ({ body: { ...customerDetail, do_not_contact: flagged ? flag : null } }),
      'GET /api/admin/customers/7/conversation': () => ({ body: null }),
      'POST /api/admin/customers/7/do-not-contact': () => {
        flagged = true
        return { body: { ...customerDetail, do_not_contact: flag } }
      },
      'POST /api/admin/customers/7/do-not-contact/lift': () => {
        flagged = false
        return { body: { ...customerDetail, do_not_contact: null } }
      },
    })
    record('/customers/7')
    expect(await screen.findByRole('button', { name: 'Start conversation' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Customer asked to stop contact' }))
    await user.type(screen.getByLabelText('What did the customer ask?'), 'Asked by phone to stop.')
    await user.click(screen.getByRole('button', { name: 'Stop all contact' }))
    expect(await screen.findByText(/Do not contact./)).toBeInTheDocument()
    expect(screen.getByText(/recorded by officer@example.com/)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Start conversation' })).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Lift' }))
    await user.type(screen.getByLabelText('Why may contact resume?'), 'The customer called back.')
    await user.click(screen.getByRole('button', { name: 'Allow contact again' }))
    expect(await screen.findByRole('button', { name: 'Start conversation' })).toBeInTheDocument()
    expect(JSON.parse(String(api.calls.find((c) => c.key.endsWith('/lift'))!.init.body))).toEqual({
      note: 'The customer called back.',
    })
  })

  it('shows why a conversation could not start', async () => {
    const user = userEvent.setup()
    mockApi({
      'GET /api/admin/customers/7': () => ({ body: customerDetail }),
      'GET /api/admin/customers/7/conversation': () => ({ body: null }),
      'POST /api/admin/customers/7/conversation': failure(503, 'llm_unreachable', 'Could not reach Azure.'),
    })
    record('/customers/7')
    await user.click(await screen.findByRole('button', { name: 'Start conversation' }))
    expect(await screen.findByText('Could not reach Azure.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start conversation' })).toBeEnabled()
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
  const reply = (overrides: Record<string, unknown> = {}) => ({
    ...answer(),
    conversation_id: 'c1',
    steps: [] as { tool: string; input: string }[],
    ...overrides,
  })
  const URL = 'POST /api/assistant/chat'

  it('shows the answer with its cited sources and the tools the assistant used', async () => {
    const user = userEvent.setup()
    const api = mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'GET /api/admin/health': () => ({ body: health }),
      [URL]: () => ({ body: reply({ steps: [{ tool: 'search_policies', input: '{"query": "plan"}' }] }) }),
    })
    page(<Playground />)

    await user.type(screen.getByLabelText('Question'), 'How much can a plan reduce the installment?{Enter}')
    expect(await screen.findByText('Generated answer')).toBeInTheDocument()
    expect(screen.getByText(/up to 50 percent/, { selector: 'p.answer-text span' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Source 1' })).toHaveAttribute('href', '#turn-1-source-1')
    expect(screen.getByText('Searched the policies')).toBeInTheDocument()

    const sources = screen.getAllByRole('listitem').filter((li) => li.className === 'source')
    expect(sources).toHaveLength(2)
    expect(within(sources[0]).getByText('hardship_policy.pdf')).toBeInTheDocument()
    expect(within(sources[0]).getByText('Page 2 · Chunk 5')).toBeInTheDocument()
    expect(within(sources[0]).getByText('Cited')).toBeInTheDocument()
    expect(within(sources[1]).queryByText(/Page/)).not.toBeInTheDocument()
    expect(within(sources[1]).queryByText('Cited')).not.toBeInTheDocument()

    expect(JSON.parse(api.calls.at(-1)!.init.body as string)).toEqual({
      message: 'How much can a plan reduce the installment?',
      conversation_id: null,
    })
  })

  it('continues the same conversation and starts a new one on request', async () => {
    const user = userEvent.setup()
    const api = mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'GET /api/admin/health': () => ({ body: health }),
      [URL]: () => ({ body: reply() }),
    })
    page(<Playground />)
    const box = screen.getByLabelText('Question')
    const sent = () => api.calls.filter((c) => c.key === URL).map((c) => JSON.parse(String(c.init.body)))

    await user.type(box, 'First question{Enter}')
    await screen.findByText('Generated answer')
    await user.type(box, 'Follow up{Enter}')
    await waitFor(() => expect(api.count(URL)).toBe(2))
    expect(sent()[1]).toEqual({ message: 'Follow up', conversation_id: 'c1' })

    await waitFor(() => expect(screen.getAllByText('Generated answer')).toHaveLength(2))
    await user.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(screen.queryByText('First question')).not.toBeInTheDocument()
    await user.type(box, 'Fresh start{Enter}')
    await waitFor(() => expect(api.count(URL)).toBe(3))
    expect(sent()[2]).toEqual({ message: 'Fresh start', conversation_id: null })
  })

  it('shows the no-evidence and retrieval-only outcomes honestly', async () => {
    const user = userEvent.setup()
    const replies = [
      reply({ answer: "I couldn't find anything in the indexed policy documents that answers this question.", answer_mode: 'no_evidence', insufficient_evidence: true, sources: [], model: null }),
      reply({ answer: 'No generated answer is available.', answer_mode: 'retrieval_only', model: null, warning: 'The answer model is not available right now: The rate limit was hit.', sources: answer().sources.map((s) => ({ ...s, cited: false })) }),
    ]
    mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'GET /api/admin/health': () => ({ body: health }),
      [URL]: () => ({ body: replies.shift() }),
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

  it('offers customer examples to administrators only', async () => {
    mockApi({
      'GET /api/admin/config': () => ({ body: config }),
      'GET /api/admin/health': () => ({ body: health }),
    })
    const view = page(<Playground />)
    expect(await screen.findByRole('button', { name: 'Which high-risk customers are overdue right now?' })).toBeInTheDocument()
    view.unmount()

    mockApi({ 'GET /api/admin/config': () => ({ body: config }) })
    page(<Playground showModelStatus={false} />)
    expect(await screen.findByRole('button', { name: 'How much can a hardship plan reduce the installment?' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Which high-risk customers are overdue right now?' })).not.toBeInTheDocument()
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
      'GET /api/admin/health': () => ({ body: health }),
      [URL]: () =>
        ++attempt === 1
          ? failure(503, 'embeddings_unavailable', 'Embedding model could not be loaded.')()
          : { body: reply() },
    })
    page(<Playground />)
    await user.type(screen.getByLabelText('Question'), 'Contact hours?{Enter}')
    expect(await screen.findByText('Embedding model could not be loaded.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Generated answer')).toBeInTheDocument()
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

describe('Audit Trail', () => {
  it('lists agent steps and officer actions, filters them and shows the details', async () => {
    const user = userEvent.setup()
    const event = (id: number, actor: string, name: string, summary: string, detail: object | null = null) => ({
      id,
      created_at: '2026-10-27T10:00:00+00:00',
      actor,
      event: name,
      customer_id: 7,
      conversation_id: 3,
      message_id: null,
      summary,
      detail,
    })
    const api = mockApi({
      'GET /api/admin/audit?limit=50': () => ({
        body: {
          events: [
            event(2, 'officer@example.com', 'draft.approved', 'Approved.'),
            event(1, 'Decision Agent', 'agent.decision', 'Recommended send reminder by email.', { model: 'gpt-4.1-mini' }),
          ],
          next_before: null,
        },
      }),
      'GET /api/admin/audit?customer_id=7&limit=50': () => ({ body: { events: [], next_before: null } }),
      'GET /api/admin/audit?customer_id=7&event=agent&limit=50': () => ({
        body: { events: [event(1, 'Decision Agent', 'agent.decision', 'Recommended send reminder.')], next_before: null },
      }),
    })
    page(<AuditTrail />)
    expect(await screen.findByText('Approved.')).toBeInTheDocument()
    expect(screen.getByText('officer@example.com')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Details' }))
    expect(screen.getByText(/"model": "gpt-4.1-mini"/)).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('Customer number'), '7')
    await user.selectOptions(screen.getByLabelText('Show'), 'agent')
    expect(await screen.findByText('Recommended send reminder.')).toBeInTheDocument()
    expect(api.count('GET /api/admin/audit?customer_id=7&event=agent&limit=50')).toBe(1)
  })
})

describe('Follow-ups', () => {
  it('lists scheduled follow-ups and runs the due ones', async () => {
    const user = userEvent.setup()
    mockApi({
      'GET /api/admin/follow-ups?status=scheduled': () => ({
        body: [
          {
            id: 9,
            conversation_id: 3,
            customer_id: 7,
            due_at: '2020-01-01T00:00:00+00:00',
            action: 'schedule_call',
            reason: 'Two emails without a reply.',
            status: 'scheduled',
            outcome: null,
            created_at: '2020-01-01T00:00:00+00:00',
            done_at: null,
          },
        ],
      }),
      'POST /api/admin/follow-ups/run-due': () => ({ body: { due: 1, done: [9], failed: [] } }),
    })
    page(<FollowUps />)
    expect(await screen.findByText('Call the customer')).toBeInTheDocument()
    expect(screen.getByText('Due now')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run due follow-ups' }))
    expect(await screen.findByText('1 of 1 due follow-up(s) done.')).toBeInTheDocument()
  })
})

describe('Approvals', () => {
  it('lists every draft, hand-over and call that waits for an officer', async () => {
    mockApi({
      'GET /api/admin/inbox': () => ({
        body: {
          drafts: [
            {
              id: 4,
              conversation_id: 3,
              customer_id: 7,
              status: 'blocked',
              subject: 'About your account',
              body: 'Dear customer, a reminder.',
              created_at: '2026-10-27T10:00:00+00:00',
            },
          ],
          escalated: [{ id: 5, customer_id: 8, status_reason: 'The customer disputes the debt.', updated_at: '2026-10-27T10:00:00+00:00' }],
          due_follow_ups: [],
          calls: [],
          total: 2,
        },
      }),
    })
    page(<Inbox />)
    expect(await screen.findByText('Blocked by the Red Team')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Customer 7' })).toHaveAttribute('href', '/customers/7')
    expect(screen.getByText(/The customer disputes the debt./)).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Calls to make' })).getByText('Nothing here.')).toBeInTheDocument()
  })

  it('says when nothing is waiting', async () => {
    mockApi({
      'GET /api/admin/inbox': () => ({ body: { drafts: [], escalated: [], due_follow_ups: [], calls: [], total: 0 } }),
    })
    page(<Inbox />)
    expect(await screen.findByText('All clear')).toBeInTheDocument()
  })
})
