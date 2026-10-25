import { useState } from 'react'
import type { CustomerMessage, DecisionAction } from '../api/types'
import { sendEmail } from '../lib/email'
import { errorMessage, formatDateTime } from '../lib/format'
import { ConfirmDialog } from './ConfirmDialog'
import { Badge, Notice } from './ui'

const ACTION_LABEL: Record<DecisionAction, string> = {
  send_reminder: 'Send a reminder',
  offer_payment_plan: 'Offer a payment plan',
  schedule_call: 'Call the customer',
  escalate_to_human: 'Escalate to a collections officer',
  no_contact: 'No contact',
}

export type MessageState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'done'; result: CustomerMessage }

/** The email drafted for one customer, with the decision, checks and policies behind it. */
export function MessageDraft({
  state,
  email,
  onRegenerate,
}: {
  state: MessageState
  /** The customer's address; without one the email cannot be sent. */
  email: string | null
  onRegenerate: () => void
}) {
  const [copied, setCopied] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState('')
  // Tied to the draft it was for: "Write again" makes a new draft, which has not been sent.
  const [sent, setSent] = useState<{ draft: CustomerMessage; at: Date; to: string; delivered: boolean } | null>(
    null,
  )

  if (state.phase === 'loading') {
    return (
      <section className="card" aria-label="Message draft">
        <h2>Message draft</h2>
        <p className="muted" role="status">
          <span className="spinner spinner-sm" aria-hidden="true" /> Choosing the action, reading the messaging policy
          and writing the email. This takes a few seconds.
        </p>
      </section>
    )
  }
  if (state.phase === 'error') {
    return (
      <section className="card" aria-label="Message draft">
        <h2>Message draft</h2>
        <Notice tone="error">Could not write the message: {state.message}</Notice>
        <div>
          <button type="button" className="btn" onClick={onRegenerate}>
            Try again
          </button>
        </div>
      </section>
    )
  }

  const m = state.result
  const decision = m.decision
  const text = [m.subject ? `Subject: ${m.subject}` : null, m.body].filter(Boolean).join('\n\n')
  const isSent = sent?.draft === m

  async function send() {
    if (!email) return
    setSending(true)
    setSendError('')
    try {
      const result = await sendEmail(m, email)
      setSent({ draft: m, at: result.sentAt, to: result.to, delivered: result.delivered })
      setConfirming(false)
    } catch (error) {
      setSendError(errorMessage(error))
    } finally {
      setSending(false)
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      /* Clipboard blocked (e.g. not HTTPS): the text can still be selected by hand. */
    }
  }

  return (
    <>
      <section className="card" aria-label="Message draft">
        <div className="card-head">
          <div>
            <h2>Message draft</h2>
            {decision ? (
              <p className="muted small">
                {ACTION_LABEL[decision.action]} · Email · {decision.priority} priority
              </p>
            ) : null}
          </div>
          <div className="page-actions">
            {m.status === 'draft' ? (
              <>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={isSent || !email}
                  title={email ? undefined : 'No email address on record'}
                  onClick={() => {
                    setSendError('')
                    setConfirming(true)
                  }}
                >
                  {isSent ? 'Sent' : 'Send email'}
                </button>
                <button type="button" className="btn" onClick={() => void copy()}>
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </>
            ) : null}
            <button type="button" className="btn" onClick={onRegenerate}>
              Write again
            </button>
          </div>
        </div>

        {m.status === 'not_written' ? (
          <Notice tone="warn">No email was written. {m.reason}</Notice>
        ) : (
          <>
            <div className="message">
              <div className="message-tags">
                <Badge tone="neutral">Email</Badge>
                <Badge tone={m.risk_level === 'high' ? 'error' : m.risk_level === 'medium' ? 'warn' : 'ok'}>
                  {m.risk_level ? `${m.risk_level[0].toUpperCase()}${m.risk_level.slice(1)}-risk tone` : 'Stage tone (not scored)'}
                </Badge>
              </div>
              {m.subject ? (
                <p className="message-subject">
                  <span className="muted">Subject:</span> {m.subject}
                </p>
              ) : null}
              <p className="message-body">{m.body}</p>
            </div>
            {isSent ? (
              <Notice tone={sent.delivered ? 'ok' : 'info'}>
                Marked as sent to {sent.to} on {formatDateTime(sent.at.toISOString())}.
                {sent.delivered
                  ? ' The email was delivered.'
                  : ` Email delivery is not connected yet, so nothing was actually emailed to ${sent.to}.`}
              </Notice>
            ) : (
              <p className="muted small">
                {email
                  ? `Draft: not sent yet. A collections officer reviews it, then sends it to ${email} with Send email.`
                  : 'Draft: not sent yet. This customer has no email address on record, so it cannot be sent.'}
              </p>
            )}
            {sendError ? <Notice tone="error">Could not send the email: {sendError}</Notice> : null}
          </>
        )}

        {decision ? (
          <div className="message-why">
            <h3>Why this action</h3>
            <ul>
              {decision.reasons.map((reason) => (
                <li key={reason.text}>{reason.text}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {m.checks.length ? (
          <div className="message-why">
            <h3>Checks passed</h3>
            <ul className="checks">
              {m.checks.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
            {m.corrected.length ? (
              <p className="muted small">Fixed on the second try: {m.corrected.join('; ')}.</p>
            ) : null}
          </div>
        ) : null}

        {m.sources.length ? (
          <details className="message-sources">
            <summary>Policies it follows ({[...new Set(m.sources.map((s) => s.filename))].join(', ')})</summary>
            {m.sources.map((s) => (
              <blockquote key={s.number}>
                <span className="muted small">
                  {s.filename}
                  {s.page_number ? `, page ${s.page_number}` : ''}
                </span>
                <p>{s.text}</p>
              </blockquote>
            ))}
          </details>
        ) : null}
      </section>
      <ConfirmDialog
        open={confirming}
        title="Send this email?"
        confirmLabel="Send email"
        busy={sending}
        onConfirm={() => void send()}
        onCancel={() => setConfirming(false)}
      >
        <p>
          To <strong>{email}</strong> (customer {m.customer_id})
        </p>
        {m.subject ? (
          <p>
            Subject: <strong>{m.subject}</strong>
          </p>
        ) : null}
        <p className="message-preview">{m.body}</p>
      </ConfirmDialog>
    </>
  )
}
