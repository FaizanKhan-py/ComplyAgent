import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { CustomerDetail, DecisionAction, Scenario, Simulation } from '../api/types'
import { errorMessage, formatPercent } from '../lib/format'
import { RiskBadge } from './RiskBadge'
import { Notice } from './ui'

const ACTION_LABEL: Record<DecisionAction, string> = {
  send_reminder: 'Send a reminder',
  offer_payment_plan: 'Offer a payment plan',
  schedule_call: 'Call the customer',
  escalate_to_human: 'Escalate to a collections officer',
  no_contact: 'No contact',
}

const STATUS_OPTIONS = [
  { value: -2, label: 'No use of the card' },
  { value: -1, label: 'Paid in full' },
  { value: 0, label: 'Revolving (minimum paid)' },
  ...Array.from({ length: 9 }, (_, i) => ({ value: i + 1, label: `${i + 1} month${i ? 's' : ''} late` })),
]

function Change({ change }: { change: number }) {
  const points = Math.round(change * 1000) / 10
  if (points === 0) return <span className="muted">no change</span>
  return (
    <span className={points > 0 ? 'change-up' : 'change-down'}>
      {points > 0 ? '+' : ''}
      {points.toFixed(1)} points
    </span>
  )
}

function ScenarioRows({ rows }: { rows: Scenario[] }) {
  return (
    <>
      {rows.map((s) => (
        <tr key={s.key}>
          <td data-label="What if">{s.label}</td>
          <td data-label="Chance of default" className="num">
            {formatPercent(s.probability)}
          </td>
          <td data-label="Change" className="num">
            <Change change={s.change} />
          </td>
          <td data-label="Risk">
            <RiskBadge level={s.risk_level} probability={s.probability} />
          </td>
        </tr>
      ))}
    </>
  )
}

/**
 * What-ifs on the risk model for one customer: ready-made payment scenarios, a fairness check
 * (only sex or education changed) and the officer's own change, optionally decided by the
 * Decision Agent. Nothing in the record changes.
 */
export function Simulator({ customer }: { customer: CustomerDetail }) {
  const ids = useId()
  const latest = customer.history[0]
  const [sim, setSim] = useState<Simulation | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState(latest?.repayment_status ?? 0)
  const [bill, setBill] = useState(String(latest?.bill_amt ?? 0))
  const [paid, setPaid] = useState(String(latest?.pay_amt ?? 0))
  const [limit, setLimit] = useState(String(customer.limit_bal))
  const [decide, setDecide] = useState(false)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])

  async function run(call: (signal: AbortSignal) => Promise<Simulation>) {
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    setBusy(true)
    setError('')
    try {
      setSim(await call(controller.signal))
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  function simulate(event: FormEvent) {
    event.preventDefault()
    void run((signal) =>
      api.simulate(
        customer.customer_id,
        {
          repayment_status: status,
          bill_amt: Number(bill) || 0,
          pay_amt: Math.max(0, Number(paid) || 0),
          limit_bal: Math.max(1, Number(limit) || customer.limit_bal),
          decide,
        },
        signal,
      ),
    )
  }

  if (!latest) return null
  const behaviour = sim?.scenarios.filter((s) => s.kind === 'behaviour') ?? []
  const fairness = sim?.scenarios.filter((s) => s.kind === 'fairness') ?? []
  const biggest = fairness.reduce((m, s) => Math.max(m, Math.abs(s.change)), 0)

  return (
    <section className="card" aria-label="Counterfactual simulator">
      <div className="card-head">
        <div>
          <h2>Counterfactual simulator</h2>
          <p className="muted small">
            What would the risk model say if this record were different? The record itself never changes.
          </p>
        </div>
        {!sim ? (
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => void run(() => api.counterfactuals(customer.customer_id))}
          >
            Run what-ifs
          </button>
        ) : null}
      </div>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {busy && !sim ? <p className="muted">Running the model…</p> : null}

      {sim ? (
        <>
          <p>
            Today: <strong>{formatPercent(sim.base.probability)}</strong> chance of default{' '}
            <RiskBadge level={sim.base.risk_level} probability={sim.base.probability} />
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">What if</th>
                  <th scope="col" className="num">
                    Chance of default
                  </th>
                  <th scope="col" className="num">
                    Change
                  </th>
                  <th scope="col">Risk</th>
                </tr>
              </thead>
              <tbody>
                <ScenarioRows rows={behaviour} />
              </tbody>
            </table>
          </div>

          <div className="message-why">
            <h3>Fairness check</h3>
            <p className="muted small">
              The same record with only a personal attribute changed. Collections decisions never use these, so any
              change here comes from the model, not from how the customer pays.{' '}
              {sim.personal_inputs
                ? sim.personal_inputs.length
                  ? `This model uses: ${sim.personal_inputs.join(', ')}.`
                  : 'This model uses no personal attribute.'
                : ''}
              {sim.personal_inputs && !sim.personal_inputs.includes('sex') && !sim.personal_inputs.includes('education')
                ? ' It was trained without sex and education, so they cannot move its prediction.'
                : ''}
            </p>
            {biggest >= 0.05 ? (
              <Notice tone="warn">
                Changing only a personal attribute moves the prediction by up to {formatPercent(biggest)}. Report
                this to the model owner.
              </Notice>
            ) : null}
            <div className="table-wrap">
              <table className="table">
                <tbody>
                  <ScenarioRows rows={fairness} />
                </tbody>
              </table>
            </div>
          </div>

          <form className="simulator-form" onSubmit={simulate}>
            <h3>Your own what-if (latest month)</h3>
            <div className="filters">
              <label className="field" htmlFor={`${ids}-status`}>
                <span>Payment status</span>
                <select id={`${ids}-status`} value={status} onChange={(e) => setStatus(Number(e.target.value))}>
                  {STATUS_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field" htmlFor={`${ids}-bill`}>
                <span>Bill (NT$)</span>
                <input id={`${ids}-bill`} inputMode="numeric" value={bill} onChange={(e) => setBill(e.target.value)} />
              </label>
              <label className="field" htmlFor={`${ids}-paid`}>
                <span>Paid (NT$)</span>
                <input id={`${ids}-paid`} inputMode="numeric" value={paid} onChange={(e) => setPaid(e.target.value)} />
              </label>
              <label className="field" htmlFor={`${ids}-limit`}>
                <span>Credit limit (NT$)</span>
                <input id={`${ids}-limit`} inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value)} />
              </label>
            </div>
            <label className="check">
              <input type="checkbox" checked={decide} onChange={(e) => setDecide(e.target.checked)} /> Also ask the
              Decision Agent what it would do
            </label>
            <div>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                Simulate
              </button>
            </div>
          </form>

          {sim.custom ? (
            <div className="message" aria-label="Your what-if">
              <p className="message-subject">
                Your what-if: {formatPercent(sim.custom.probability)} chance of default (<Change change={sim.custom.change} />
                ) <RiskBadge level={sim.custom.risk_level} probability={sim.custom.probability} />
              </p>
              {sim.custom.recommendation ? (
                <>
                  <p>
                    The Decision Agent would: <strong>{ACTION_LABEL[sim.custom.recommendation.action]}</strong> (
                    {sim.custom.recommendation.priority} priority)
                  </p>
                  <ul>
                    {sim.custom.recommendation.reasons.map((r) => (
                      <li key={r.text}>{r.text}</li>
                    ))}
                  </ul>
                </>
              ) : null}
              {sim.custom.decision_error ? (
                <p className="muted small">The Decision Agent could not decide: {sim.custom.decision_error}</p>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
