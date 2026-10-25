import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { CustomerDetail } from '../api/types'
import { MessageDraft, type MessageState } from '../components/MessageDraft'
import { PolicyGuidance } from '../components/PolicyGuidance'
import { PaymentStrip, ProbabilityGauge } from '../components/visuals'
import { ActualBadge, PaymentBadge, RiskBadge } from '../components/RiskBadge'
import { ErrorState, Loading, Notice, PageHeader } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { errorMessage, formatMoney, formatPercent } from '../lib/format'

function monthsLate(months: number): string {
  return `${months} month${months === 1 ? '' : 's'}`
}

const SPLIT_NOTE = {
  train: 'This customer was used to train the model, so the prediction is not an independent test.',
  validation: 'This customer was held back to validate the model; it was not trained on them.',
  test: 'This customer was held back for testing; the model was not trained on them.',
}

function Prediction({ c }: { c: CustomerDetail }) {
  return (
    <section className="card card-verdict" aria-label="Risk prediction">
      <h2>Risk prediction</h2>
      {c.default_probability !== null && c.risk_level !== null ? (
        <ProbabilityGauge probability={c.default_probability} level={c.risk_level} />
      ) : null}
      {c.default_probability === null ? (
        <p className="muted">This customer has not been scored. Train the model to see a prediction.</p>
      ) : (
        <>
          <dl className="details">
            <div>
              <dt>Chance of default</dt>
              <dd>
                <RiskBadge level={c.risk_level} probability={c.default_probability} />
              </dd>
            </div>
            <div>
              <dt>Model's call</dt>
              <dd>{c.predicted_default ? 'Will default' : 'Will not default'}</dd>
            </div>
            <div>
              <dt>Actual status</dt>
              <dd>
                <ActualBadge defaulted={c.actual_default} />
              </dd>
            </div>
          </dl>
          {c.data_split ? <p className="muted small">{SPLIT_NOTE[c.data_split]}</p> : null}
        </>
      )}
    </section>
  )
}

export function CustomerRecord() {
  const id = Number(useParams().customerId)
  const record = useResource(() => api.getCustomer(id))
  const c = record.data
  // `created`/`scored`: set by the "Add customer" page after saving. `from`: the list, with its
  // filters, that the person opened this record from.
  const state = useLocation().state as { created?: boolean; scored?: boolean; from?: string } | null
  const created = state
  const back = state?.from?.startsWith('/customers') ? state.from : '/customers'

  // The draft belongs to one customer: opening another record hides it, leaving cancels it.
  const [drafted, setDrafted] = useState<{ id: number; state: MessageState } | null>(null)
  const message = drafted?.id === id ? drafted.state : null
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [id])

  async function generate() {
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setDrafted({ id, state: { phase: 'loading' } })
    try {
      const result = await api.generateMessage(id, controller.signal)
      setDrafted({ id, state: { phase: 'done', result } })
    } catch (error) {
      if (!controller.signal.aborted) setDrafted({ id, state: { phase: 'error', message: errorMessage(error) } })
    }
  }

  return (
    <>
      <Link className="back-link" to={back}>
        ← Customer Records
      </Link>
      {record.loading ? (
        <Loading label="Loading the customer…" />
      ) : record.error && !c ? (
        <ErrorState error={record.error} onRetry={record.refresh} />
      ) : c ? (
        <>
          {created?.created ? (
            <Notice tone="ok">
              Customer {c.customer_id} was added to the database
              {created.scored ? ' and scored by the risk model.' : '. Train the model to give them a prediction.'}
            </Notice>
          ) : null}
          <PageHeader
            title={`Customer ${c.customer_id}`}
            eyebrow="Account file"
            description={
              c.latest_status !== null && c.latest_status > 0
                ? `Credit card account · overdue this month (${monthsLate(c.latest_status)} late on the Sep 2005 bill)`
                : 'Credit card account · not overdue this month'
            }
            actions={
              c.latest_status !== null && c.latest_status > 0 ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={message?.phase === 'loading'}
                  onClick={() => void generate()}
                >
                  {message?.phase === 'loading' ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
                  Generate message
                </button>
              ) : null
            }
          />
          {message ? <MessageDraft state={message} email={c.email} onRegenerate={() => void generate()} /> : null}
          <div className="grid grid-2">
            <section className="card" aria-label="Account details">
              <h2>Account details</h2>
              <dl className="details">
                <div>
                  <dt>Email</dt>
                  <dd>{c.email ?? <span className="muted">Not on record</span>}</dd>
                </div>
                <div>
                  <dt>Credit limit</dt>
                  <dd>{formatMoney(c.limit_bal)}</dd>
                </div>
                <div>
                  <dt>Age</dt>
                  <dd>{c.age}</dd>
                </div>
                <div>
                  <dt>Sex</dt>
                  <dd>{c.sex}</dd>
                </div>
                <div>
                  <dt>Education</dt>
                  <dd>{c.education}</dd>
                </div>
                <div>
                  <dt>Marital status</dt>
                  <dd>{c.marriage}</dd>
                </div>
              </dl>
            </section>
            <Prediction c={c} />
          </div>

          <section className="card" aria-label="Payment history">
            <div>
              <h2>Payment history</h2>
              <p className="muted small">
                {c.on_time_payment_rate === null || c.avg_payment_delay === null || c.avg_credit_utilization === null
                  ? 'The last six months.'
                  : `Paid in full ${formatPercent(c.on_time_payment_rate)} of the months that count · average delay ${c.avg_payment_delay.toFixed(1)} months · average ${formatPercent(c.avg_credit_utilization)} of the credit limit used.`}
              </p>
            </div>
            <PaymentStrip months={c.history.map((h) => ({ month: h.month, status: h.repayment_status }))} />
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Month</th>
                    <th scope="col">Payment</th>
                    <th scope="col" className="num">Bill</th>
                    <th scope="col" className="num">Paid</th>
                  </tr>
                </thead>
                <tbody>
                  {c.history.map((h) => (
                    <tr key={h.month_no}>
                      <td data-label="Month">{h.month}</td>
                      <td data-label="Payment">
                        <PaymentBadge status={h.repayment_status} />
                      </td>
                      <td data-label="Bill" className="num">{formatMoney(h.bill_amt)}</td>
                      <td data-label="Paid" className="num">{formatMoney(h.pay_amt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <PolicyGuidance key={c.customer_id} customerId={c.customer_id} />
        </>
      ) : null}
    </>
  )
}
