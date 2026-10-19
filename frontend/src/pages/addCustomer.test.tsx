import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { customerDetail, mockApi, portfolio } from '../test/fixtures'
import { AddCustomer } from './AddCustomer'
import { CustomerRecord } from './CustomerRecord'

afterEach(() => vi.unstubAllGlobals())

const open = () =>
  render(
    <MemoryRouter initialEntries={['/customers/new']}>
      <Routes>
        <Route path="/customers/new" element={<AddCustomer />} />
        <Route path="/customers/:customerId" element={<CustomerRecord />} />
      </Routes>
    </MemoryRouter>,
  )

describe('Add customer', () => {
  it('points out every missing field and sends nothing', async () => {
    const api = mockApi({ 'GET /api/admin/portfolio/summary': () => ({ body: portfolio() }) })
    const user = userEvent.setup()
    open()
    await screen.findByText(/Scored by XGBoost \(tuned\)/)
    await user.click(screen.getByRole('button', { name: 'Save customer' }))

    expect(screen.getByText('Check the highlighted fields and try again.')).toBeInTheDocument()
    expect(screen.getByLabelText('Credit limit (NT$)')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('Enter an age from 18 to 120.')).toBeInTheDocument()
    expect(screen.getByText('Choose what happened the following month.')).toBeInTheDocument()
    expect(api.count('POST /api/admin/customers')).toBe(0)
  })

  it('saves the sample customer and opens the new record', async () => {
    let sent: unknown
    mockApi({
      'GET /api/admin/portfolio/summary': () => ({ body: portfolio() }),
      'POST /api/admin/customers': (init) => {
        sent = JSON.parse(String(init.body))
        return { status: 201, body: { customer: { ...customerDetail, customer_id: 201 }, scored: true } }
      },
      'GET /api/admin/customers/201': () => ({ body: { ...customerDetail, customer_id: 201 } }),
    })
    const user = userEvent.setup()
    open()
    await user.click(screen.getByRole('button', { name: 'Fill with sample values' }))
    const summary = within(screen.getByRole('region', { name: 'What the model will see' }))
    expect(summary.getByText('Average delay').nextSibling).toHaveTextContent('0.5 months')
    await user.click(screen.getByRole('button', { name: 'Save customer' }))

    expect(await screen.findByRole('heading', { name: 'Customer 201' })).toBeInTheDocument()
    expect(screen.getByText(/was added to the database and scored by the risk model/)).toBeInTheDocument()
    expect(sent).toMatchObject({
      limit_bal: 120000,
      sex: 'Female',
      education_code: 2,
      marriage: 'Married',
      age: 38,
      default_next_month: false,
    })
    expect((sent as { history: unknown[] }).history[0]).toEqual({ repayment_status: 2, bill_amt: 96400, pay_amt: 0 })
    expect(sent).not.toHaveProperty('customer_id')
  })

  it('shows a taken customer number on its field', async () => {
    mockApi({
      'GET /api/admin/portfolio/summary': () => ({ body: { ...portfolio(), model: null } }),
      'POST /api/admin/customers': () => ({
        status: 409,
        body: { error: { code: 'customer_exists', message: 'Customer 7 already exists. Use another number or leave it blank.' } },
      }),
    })
    const user = userEvent.setup()
    open()
    expect(await screen.findByText(/saved without a prediction/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Fill with sample values' }))
    await user.type(screen.getByLabelText('Customer number'), '7')
    await user.click(screen.getByRole('button', { name: 'Save customer' }))

    expect(await screen.findByText(/Customer 7 already exists/)).toBeInTheDocument()
    expect(screen.getByLabelText('Customer number')).toHaveAttribute('aria-invalid', 'true')
  })
})
