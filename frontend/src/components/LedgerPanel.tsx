import { useEffect, useId, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { Ledger } from '../api/types'
import { errorMessage, formatDateTime, formatMoney } from '../lib/format'
import { Badge, Notice } from './ui'

const PROMISE_TONE = { open: 'warn', kept: 'ok', broken: 'error', cancelled: 'neutral' } as const
const OUTCOME_LABEL = { sent: 'Email sent', reached: 'Spoke to the customer', no_answer: 'No answer', voicemail: 'Voicemail' }

function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

type Form = null | 'payment' | 'promise' | 'call'

/**
 * What happened after the records end: payments, promises to pay and contact attempts, with the
 * contact rules and whether the customer may be contacted right now. A payment lowers what the
 * emails ask for; a promise is checked by the Follow-up Agent; a call counts toward the limit.
 */
export function LedgerPanel({ customerId, onChange }: { customerId: number; onChange?: () => void }) {
  const ids = useId()
  const [ledger, setLedger] = useState<Ledger | null>(null)
  const [error, setError] = useState('')
  const [form, setForm] = useState<Form>(null)
  const [busy, setBusy] = useState(false)
  const [amount, setAmount] = useState('')
  const [day, setDay] = useState(today())
  const [outcome, setOutcome] = useState<'reached' | 'no_answer' | 'voicemail'>('reached')
  const [note, setNote] = useState('')

  useEffect(() => {
    let alive = true
    api.ledger(customerId).then(
      (l) => alive && setLedger(l),
      (err: unknown) => alive && setError(errorMessage(err)),
    )
    return () => {
      alive = false
    }
  }, [customerId])

  function open(next: Form) {
    setForm(form === next ? null : next)
    setAmount('')
    setNote('')
    setDay(today())
    setError('')
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const value = Number(amount.replace(/[,\s]/g, ''))
      const next =
        form === 'payment'
          ? await api.recordPayment(customerId, { amount: value, paid_on: day, ...(note.trim() ? { note: note.trim() } : {}) })
          : form === 'promise'
            ? await api.recordPromise(customerId, { due_on: day, ...(value > 0 ? { amount: value } : {}) })
            : await api.recordContact(customerId, { outcome, ...(note.trim() ? { note: note.trim() } : {}) })
      setLedger(next)
      setForm(null)
      onChange?.()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (!ledger) return error ? <Notice tone="error">{error}</Notice> : null
  const openPromise = ledger.promises.find((p) => p.status === 'open')

  return (
    <section className="card" aria-label="Collections ledger">
      <div className="card-head">
        <div>
          <h2>Collections ledger</h2>
          <p className="muted small">Payments, promises and contact attempts since the records end.</p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn btn-sm" onClick={() => open('payment')}>
            Record a payment
          </button>
          <button type="button" className="btn btn-sm" onClick={() => open('promise')}>
            Record a promise
          </button>
          <button type="button" className="btn btn-sm" onClick={() => open('call')}>
            Log a call
          </button>
        </div>
      </div>

      {ledger.contact_now.length ? (
        <Notice tone="warn">
          <strong>Do not contact right now:</strong> {ledger.contact_now.join('; ')}.
        </Notice>
      ) : (
        <p className="small">
          <Badge tone="ok">Contact allowed now</Badge> <span className="muted">{ledger.contact_rules}</span>
        </p>
      )}

      {form ? (
        <form className="ledger-form" onSubmit={submit}>
          {form !== 'call' ? (
            <label className="field" htmlFor={`${ids}-amount`}>
              {form === 'payment' ? 'Amount paid (NT$)' : 'Amount promised (NT$, optional)'}
              <input
                id={`${ids}-amount`}
                inputMode="numeric"
                value={amount}
                required={form === 'payment'}
                onChange={(e) => setAmount(e.target.value)}
              />
            </label>
          ) : (
            <label className="field" htmlFor={`${ids}-outcome`}>
              How did the call go?
              <select id={`${ids}-outcome`} value={outcome} onChange={(e) => setOutcome(e.target.value as typeof outcome)}>
                <option value="reached">Spoke to the customer</option>
                <option value="no_answer">No answer</option>
                <option value="voicemail">Left a voicemail</option>
              </select>
            </label>
          )}
          {form !== 'call' ? (
            <label className="field" htmlFor={`${ids}-day`}>
              {form === 'payment' ? 'Paid on' : 'Promised by'}
              <input id={`${ids}-day`} type="date" value={day} required onChange={(e) => setDay(e.target.value)} />
            </label>
          ) : null}
          {form !== 'promise' ? (
            <label className="field" htmlFor={`${ids}-note`}>
              Note (optional)
              <input id={`${ids}-note`} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
            </label>
          ) : null}
          <div className="page-actions">
            <button type="submit" className="btn btn-primary" disabled={busy}>
              Save
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setForm(null)}>
              Cancel
            </button>
          </div>
          {form === 'call' && ledger.contact_now.length ? (
            <p className="muted small">The contact rules say not now; the call is still logged, and the audit trail notes it.</p>
          ) : null}
        </form>
      ) : null}
      {error ? <Notice tone="error">{error}</Notice> : null}

      <div className="grid grid-2">
        <div className="message-why">
          <h3>Payments ({formatMoney(ledger.paid_total)} in total)</h3>
          {ledger.payments.length ? (
            <ul>
              {ledger.payments.map((p) => (
                <li key={p.id}>
                  {formatMoney(p.amount)} on {p.paid_on}
                  <span className="muted small"> · {p.recorded_by}{p.note ? ` · ${p.note}` : ''}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">None recorded.</p>
          )}
        </div>
        <div className="message-why">
          <h3>Promises to pay</h3>
          {openPromise ? (
            <p className="small">
              Open: {openPromise.amount ? formatMoney(openPromise.amount) : 'a payment'} by {openPromise.due_on}. The
              Follow-up Agent checks it the day after.
            </p>
          ) : null}
          {ledger.promises.length ? (
            <ul>
              {ledger.promises.map((p) => (
                <li key={p.id}>
                  <Badge tone={PROMISE_TONE[p.status]}>{p.status}</Badge> {p.amount ? formatMoney(p.amount) : 'Payment'} by{' '}
                  {p.due_on}
                  <span className="muted small"> · {p.recorded_by}{p.outcome ? ` · ${p.outcome}` : ''}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">None recorded.</p>
          )}
        </div>
      </div>
      {ledger.contacts.length ? (
        <details className="message-sources">
          <summary>Contact attempts ({ledger.contacts.length})</summary>
          <ul>
            {ledger.contacts.map((c) => (
              <li key={c.id}>
                {formatDateTime(c.made_at)} · {c.channel} · {OUTCOME_LABEL[c.outcome]}
                <span className="muted small"> · {c.made_by}{c.note ? ` · ${c.note}` : ''}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  )
}
