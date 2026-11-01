import { useId, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { CustomerDetail } from '../api/types'
import { errorMessage, formatDateTime } from '../lib/format'
import { Notice } from './ui'

/**
 * The customer's request to stop contact. While it is on record no email is written, approved,
 * sent or followed up. Recording or lifting it needs a reason, and both go to the audit trail.
 */
export function DoNotContact({ customer, onChange }: { customer: CustomerDetail; onChange: () => void }) {
  const id = useId()
  const flag = customer.do_not_contact
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!text.trim()) return
    setBusy(true)
    setError('')
    try {
      if (flag) await api.liftDoNotContact(customer.customer_id, text.trim())
      else await api.doNotContact(customer.customer_id, text.trim())
      setText('')
      setOpen(false)
      onChange()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const form = open ? (
    <form className="dnc-form" onSubmit={submit}>
      <label className="field" htmlFor={id}>
        {flag ? 'Why may contact resume?' : 'What did the customer ask?'}
        <input
          id={id}
          value={text}
          maxLength={500}
          placeholder={flag ? 'e.g. The customer called and asked us to email again' : 'e.g. Asked by phone to stop all contact'}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <div className="page-actions">
        <button type="submit" className={flag ? 'btn' : 'btn btn-danger'} disabled={busy || !text.trim()}>
          {flag ? 'Allow contact again' : 'Stop all contact'}
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  ) : null

  if (flag) {
    return (
      <Notice tone="warn">
        <strong>Do not contact.</strong> {flag.reason} (recorded by {flag.set_by}, {formatDateTime(flag.set_at)}). No
        email can be written or sent until an officer lifts this.{' '}
        {!open ? (
          <button type="button" className="btn btn-sm" onClick={() => setOpen(true)}>
            Lift
          </button>
        ) : null}
        {form}
        {error ? <p className="field-error">{error}</p> : null}
      </Notice>
    )
  }
  return (
    <div className="dnc">
      {!open ? (
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(true)}>
          Customer asked to stop contact
        </button>
      ) : null}
      {form}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </div>
  )
}
