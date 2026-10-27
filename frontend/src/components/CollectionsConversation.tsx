import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { useCurrentUser } from '../auth'
import type {
  CaseFile,
  Conversation,
  ConversationMessage,
  DecisionAction,
  FollowUp,
  Recommendation,
  ReviewRound,
} from '../api/types'
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

const SITUATION_LABEL: Record<CaseFile['analysis']['situation'], string> = {
  paid_up: 'Paid up',
  early_arrears: 'Early arrears',
  persistent_arrears: 'Persistent arrears',
  worsening: 'Getting worse',
  improving: 'Improving',
  partial_payer: 'Pays part of each bill',
  stopped_paying: 'Stopped paying',
}

const INTENT_LABEL: Record<string, string> = {
  promise_to_pay: 'Promise to pay',
  payment_made: 'Says they paid',
  hardship: 'Hardship',
  dispute: 'Dispute',
  question: 'Question',
  callback_request: 'Wants a call',
  stop_contact: 'Asks to stop contact',
  complaint: 'Complaint',
  other: 'Other',
}

/** The agents each request runs, with when each usually starts (ms): an estimate, since the
 * backend answers in one request and sends no progress. */
const START_STEPS = [
  { label: 'Case Analyst reads the account', from: 0 },
  { label: 'Decision Agent chooses the action', from: 2500 },
  { label: 'Writing the email', from: 5000 },
  { label: 'Red Team attacks the draft', from: 8000 },
]
const REPLY_STEPS = [
  { label: 'Reading the conversation', from: 0 },
  { label: 'Writing the reply', from: 1500 },
  { label: 'Red Team attacks the draft', from: 4500 },
]

export function AgentProgress({ steps, label }: { steps: { label: string; from: number }[]; label: string }) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const started = Date.now()
    const timer = window.setInterval(() => setElapsed(Date.now() - started), 200)
    return () => window.clearInterval(timer)
  }, [])
  const total = steps[steps.length - 1].from + 3000
  const percent = Math.round(95 * (1 - Math.exp(-elapsed / (total / 2))))
  const current = steps.findLastIndex((step) => elapsed >= step.from)
  return (
    <div className="progress-block">
      <div
        className="progress"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${steps[current].label}, about ${percent}%`}
      >
        <div className="progress-fill" style={{ width: `${percent}%` }} />
      </div>
      <ol className="progress-steps">
        {steps.map((step, i) => (
          <li key={step.label} className={i < current ? 'step-done' : i === current ? 'step-now' : undefined}>
            {step.label}
          </li>
        ))}
      </ol>
    </div>
  )
}

function CaseFileView({ file, decision }: { file: CaseFile; decision: Recommendation }) {
  const a = file.analysis
  return (
    <details className="message-sources" open>
      <summary>Case file and decision</summary>
      <div className="case-file">
        <p>{a.summary}</p>
        <div className="message-tags">
          <Badge tone="info">{SITUATION_LABEL[a.situation]}</Badge>
          <Badge tone={a.urgency === 'critical' || a.urgency === 'high' ? 'error' : a.urgency === 'medium' ? 'warn' : 'ok'}>
            {a.urgency} urgency
          </Badge>
          <Badge tone="neutral">
            Decision: {ACTION_LABEL[decision.action]} · {decision.priority} priority
          </Badge>
        </div>
        <div className="message-why">
          <h3>What the payments show</h3>
          <ul>
            {a.patterns.map((p) => (
              <li key={p.text}>{p.text}</li>
            ))}
          </ul>
        </div>
        {a.care_flags.length ? (
          <div className="message-why">
            <h3>Handle with care</h3>
            <ul>
              {a.care_flags.map((p) => (
                <li key={p.text}>{p.text}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="message-why">
          <h3>Why this action</h3>
          <ul>
            {decision.reasons.map((r) => (
              <li key={r.text}>{r.text}</li>
            ))}
          </ul>
        </div>
        {a.open_questions.length ? (
          <div className="message-why">
            <h3>Ask the customer</h3>
            <ul>
              {a.open_questions.map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </details>
  )
}

function RoundView({ round }: { round: ReviewRound }) {
  return (
    <li>
      <strong>
        Round {round.round}: {round.checked_by === 'guardrails' ? 'Fixed rules' : 'Red Team'}{' '}
      </strong>
      <Badge tone={round.passed ? 'ok' : 'error'}>{round.passed ? 'Passed' : 'Sent back'}</Badge>
      {round.issues.length ? (
        <ul>
          {round.issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      ) : null}
      {round.findings.length ? (
        <ul>
          {round.findings.map((f, i) => (
            <li key={i}>
              <Badge tone={f.severity === 'blocker' ? 'error' : 'warn'}>{f.severity}</Badge> “{f.quote}”: {f.issue}{' '}
              <span className="muted">Fix: {f.fix}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {round.decision_revised ? (
        <p className="muted small">
          The Decision Agent changed its action from {round.decision_revised.from.replace(/_/g, ' ')} to{' '}
          {round.decision_revised.to.replace(/_/g, ' ')}.
        </p>
      ) : null}
      {round.dropped ? (
        <p className="muted small">
          {round.dropped} finding{round.dropped === 1 ? '' : 's'} dropped: quoted words that are not in the email.
        </p>
      ) : null}
    </li>
  )
}

const DRAFT_STATUS: Record<string, { label: string; tone: 'ok' | 'warn' | 'error' | 'info' | 'neutral' }> = {
  pending_approval: { label: 'Waiting for approval', tone: 'warn' },
  awaiting_supervisor: { label: 'Waiting for a team lead', tone: 'warn' },
  blocked: { label: 'Blocked by the Red Team', tone: 'error' },
  approved: { label: 'Approved, ready to send', tone: 'ok' },
  rejected: { label: 'Rejected', tone: 'neutral' },
  superseded: { label: 'Replaced by a newer draft', tone: 'neutral' },
  sent: { label: 'Sent', tone: 'info' },
}

/** One email to the customer: a draft with its review and the officer's actions, or a sent one. */
function Outbound({
  message,
  email,
  busy,
  onAct,
  onRedraft,
}: {
  message: ConversationMessage
  email: string | null
  busy: boolean
  onAct: (run: () => Promise<Conversation>) => Promise<boolean>
  onRedraft: (feedback: string | null) => void
}) {
  const ids = useId()
  const [editing, setEditing] = useState(false)
  const [subject, setSubject] = useState(message.subject ?? '')
  const [body, setBody] = useState(message.body)
  const [note, setNote] = useState('')
  const [confirmSend, setConfirmSend] = useState(false)
  const user = useCurrentUser()
  const status = DRAFT_STATUS[message.status]
  const live = ['pending_approval', 'awaiting_supervisor', 'blocked', 'approved'].includes(message.status)
  const secondStep = message.status === 'awaiting_supervisor'
  // Four eyes: only a team lead who did not give the first approval can give the second.
  const canSecond = Boolean(user?.supervisor) && user?.email !== message.first_approved_by

  if (!live && message.status !== 'sent') {
    return (
      <li className="thread-item thread-out thread-old">
        <span className="muted small">
          Draft {status.label.toLowerCase()}
          {message.reviewed_by ? ` by ${message.reviewed_by}` : ''}
          {message.review_note ? `: “${message.review_note}”` : ''}
        </span>
      </li>
    )
  }

  const review = message.review
  return (
    <li className="thread-item thread-out">
      <div className="thread-head">
        <span className="thread-who">To the customer</span>
        <Badge tone={status.tone}>{status.label}</Badge>
        {message.edited ? <Badge tone="neutral">Edited by an officer</Badge> : null}
        {message.needs_supervisor && message.status !== 'sent' ? (
          <Badge tone="info">Needs a team lead (four eyes)</Badge>
        ) : null}
      </div>
      {editing ? (
        <div className="thread-edit">
          <label className="field" htmlFor={`${ids}-subject`}>
            Subject
            <input id={`${ids}-subject`} value={subject} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <label className="field" htmlFor={`${ids}-body`}>
            Body
            <textarea id={`${ids}-body`} rows={10} value={body} onChange={(e) => setBody(e.target.value)} />
          </label>
        </div>
      ) : (
        <div className="message">
          {message.subject ? (
            <p className="message-subject">
              <span className="muted">Subject:</span> {message.subject}
            </p>
          ) : null}
          <p className="message-body">{message.body}</p>
        </div>
      )}

      {message.status === 'sent' ? (
        <p className="muted small">
          Sent by {message.sent_by} on {formatDateTime(message.sent_at)}, approved by {message.reviewed_by}
          {message.first_approved_by ? ` after ${message.first_approved_by}` : ''}.{' '}
          {message.delivered
            ? 'Emailed through Microsoft 365.'
            : 'Email delivery is not connected, so this records the send; nothing was emailed.'}
        </p>
      ) : null}
      {secondStep ? (
        <p className="muted small">
          First approved by {message.first_approved_by} on {formatDateTime(message.first_approved_at ?? null)}. This
          email offers a payment plan or answers a hardship report, so a team lead must approve it too.
          {!canSecond ? ' You cannot give the second approval: ask a team lead.' : ''}
        </p>
      ) : null}
      {message.status === 'approved' ? (
        <p className="muted small">
          Approved by {message.reviewed_by} on {formatDateTime(message.reviewed_at)}
          {message.review_note ? `: “${message.review_note}”` : '.'}
        </p>
      ) : null}

      {review ? (
        <details className="message-sources" open={message.status === 'blocked'}>
          <summary>
            Compliance review: {review.rounds.length} round{review.rounds.length === 1 ? '' : 's'}
            {message.status === 'blocked' ? ', still not compliant' : ''}
          </summary>
          <ol className="review-rounds">
            {review.rounds.map((r) => (
              <RoundView key={r.round} round={r} />
            ))}
          </ol>
          {review.checks.length ? (
            <ul className="checks">
              {review.checks.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          ) : null}
        </details>
      ) : null}

      {live ? (
        <div className="thread-actions">
          {message.status !== 'approved' ? (
            <label className="field" htmlFor={`${ids}-note`}>
              Note or feedback {message.status !== 'blocked' ? '(needed to reject)' : ''}
              <input
                id={`${ids}-note`}
                value={note}
                placeholder="e.g. Be warmer, mention the payment plan"
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
          ) : null}
          <div className="page-actions">
            {message.status === 'pending_approval' ? (
              <>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() =>
                    void onAct(() =>
                      api.approveMessage(message.id, {
                        ...(editing ? { subject, body } : {}),
                        ...(note.trim() ? { note: note.trim() } : {}),
                      }),
                    ).then((ok) => ok && setEditing(false))
                  }
                >
                  {editing ? 'Approve the edit' : message.needs_supervisor ? 'Approve (first of two)' : 'Approve'}
                </button>
                <button type="button" className="btn" disabled={busy} onClick={() => setEditing(!editing)}>
                  {editing ? 'Cancel edit' : 'Edit'}
                </button>
              </>
            ) : null}
            {secondStep ? (
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || !canSecond}
                title={canSecond ? undefined : 'A different person who is a team lead must approve it'}
                onClick={() =>
                  void onAct(() => api.approveMessage(message.id, note.trim() ? { note: note.trim() } : {}))
                }
              >
                Approve as team lead
              </button>
            ) : null}
            {message.status === 'approved' ? (
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || !email}
                title={email ? undefined : 'No email address on record'}
                onClick={() => setConfirmSend(true)}
              >
                Send email
              </button>
            ) : null}
            {message.status !== 'approved' ? (
              <button type="button" className="btn" disabled={busy} onClick={() => onRedraft(note.trim() || null)}>
                Write again{note.trim() ? ' with this feedback' : ''}
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn-ghost"
              disabled={busy || !note.trim()}
              title={note.trim() ? undefined : 'Write why in the note first'}
              onClick={() => void onAct(() => api.rejectMessage(message.id, note.trim()))}
            >
              Reject
            </button>
          </div>
        </div>
      ) : null}
      {message.status === 'approved' ? (
        <ConfirmDialog
          open={confirmSend}
          title="Send this email?"
          confirmLabel="Send email"
          busy={busy}
          onConfirm={() => void onAct(() => api.sendMessage(message.id)).then(() => setConfirmSend(false))}
          onCancel={() => setConfirmSend(false)}
        >
          <p>
            To <strong>{email}</strong>
          </p>
          <p className="message-preview">{message.body}</p>
        </ConfirmDialog>
      ) : null}
    </li>
  )
}

function Inbound({ message }: { message: ConversationMessage }) {
  return (
    <li className="thread-item thread-in">
      <div className="thread-head">
        <span className="thread-who">From the customer</span>
        {message.intent ? <Badge tone="info">{INTENT_LABEL[message.intent] ?? message.intent}</Badge> : null}
        <span className="muted small">{formatDateTime(message.created_at)}</span>
      </div>
      <div className="message message-in">
        {message.subject ? (
          <p className="message-subject">
            <span className="muted">Subject:</span> {message.subject}
          </p>
        ) : null}
        <p className="message-body">{message.body}</p>
      </div>
      {message.intent_summary ? <p className="muted small">{message.intent_summary}</p> : null}
    </li>
  )
}

const FOLLOW_UP_LABEL: Record<FollowUp['action'], string> = {
  send_reminder: 'send a reminder',
  schedule_call: 'call the customer',
  escalate_to_human: 'hand the account to a person',
}

/** The Follow-up Agent's plan: the scheduled follow-up, and the last one that ran. */
function FollowUpView({
  followUps,
  busy,
  onRun,
}: {
  followUps: FollowUp[]
  busy: boolean
  onRun: (id: number) => void
}) {
  const scheduled = followUps.find((f) => f.status === 'scheduled')
  const last = [...followUps].reverse().find((f) => f.status === 'done')
  if (!scheduled && !last) return null
  return (
    <div className="follow-up" aria-label="Follow-up">
      {scheduled ? (
        <p>
          <strong>Follow-up Agent:</strong> if there is no reply by {formatDateTime(scheduled.due_at)},{' '}
          {FOLLOW_UP_LABEL[scheduled.action]}. <span className="muted">{scheduled.reason}</span>{' '}
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => onRun(scheduled.id)}>
            Run now
          </button>
        </p>
      ) : null}
      {last?.outcome ? (
        <p className="muted small">
          Last follow-up ({formatDateTime(last.done_at)}): {last.outcome}
        </p>
      ) : null}
    </div>
  )
}

type Busy = null | 'start' | 'reply' | 'action'

/**
 * The customer's collections conversation. Starting it runs every agent (Case Analyst, Decision
 * Agent, writer and Red Team); each customer reply runs the Communication Agent. Nothing is sent
 * until an officer approves it.
 */
export function CollectionsConversation({
  customerId,
  email,
  overdue,
  blocked = false,
}: {
  customerId: number
  email: string | null
  overdue: boolean
  /** The customer asked not to be contacted: nothing can be started. */
  blocked?: boolean
}) {
  const ids = useId()
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState('')
  const [replySubject, setReplySubject] = useState('')
  const [replyBody, setReplyBody] = useState('')
  const abort = useRef<AbortController | null>(null)

  useEffect(() => {
    let alive = true
    api.conversation(customerId).then(
      (c) => {
        if (!alive) return
        setConversation(c)
        setLoaded(true)
      },
      (err: unknown) => {
        if (!alive) return
        setError(errorMessage(err))
        setLoaded(true)
      },
    )
    return () => {
      alive = false
      abort.current?.abort()
    }
  }, [customerId])

  async function run(kind: Busy, call: (signal: AbortSignal) => Promise<Conversation>): Promise<boolean> {
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    setBusy(kind)
    setError('')
    try {
      setConversation(await call(controller.signal))
      return true
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err))
      return false
    } finally {
      if (!controller.signal.aborted) setBusy(null)
    }
  }

  const act = (call: () => Promise<Conversation>) => run('action', () => call())

  function addReply(event: FormEvent) {
    event.preventDefault()
    if (!conversation || !replyBody.trim()) return
    const body = { body: replyBody.trim(), ...(replySubject.trim() ? { subject: replySubject.trim() } : {}) }
    void run('reply', (signal) => api.customerReplied(conversation.id, body, signal)).then((ok) => {
      if (ok) {
        setReplyBody('')
        setReplySubject('')
      }
    })
  }

  if (!loaded) return null
  const c = conversation
  const closed = c?.status === 'closed'

  return (
    <section className="card" aria-label="Collections conversation">
      <div className="card-head">
        <div>
          <h2>Collections conversation</h2>
          <p className="muted small">
            The agents draft every email; a collections officer approves it before it is sent.
          </p>
        </div>
        <div className="page-actions">
          {c ? (
            <Badge tone={c.status === 'open' ? 'ok' : c.status === 'escalated' ? 'error' : 'neutral'}>
              {c.status === 'open' ? 'Open' : c.status === 'escalated' ? 'With a person' : 'Closed'}
            </Badge>
          ) : null}
          {(!c || closed) && overdue && !blocked ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy !== null}
              onClick={() => void run('start', (signal) => api.startConversation(customerId, signal))}
            >
              {c ? 'Start a new conversation' : 'Start conversation'}
            </button>
          ) : null}
          {c && !closed ? (
            <button
              type="button"
              className="btn"
              disabled={busy !== null}
              onClick={() => void act(() => api.closeConversation(c.id, null))}
            >
              Close
            </button>
          ) : null}
        </div>
      </div>

      {error ? <Notice tone="error">{error}</Notice> : null}
      {busy === 'start' ? <AgentProgress steps={START_STEPS} label="Running the agents" /> : null}
      {!c && busy !== 'start' ? (
        <p className="muted">
          {overdue
            ? 'No conversation yet. Starting one analyses the case, chooses the action and drafts the first email.'
            : 'Nothing is overdue, so there is nothing to collect.'}
        </p>
      ) : null}

      {c && busy !== 'start' ? (
        <>
          {c.status !== 'open' && c.status_reason ? (
            <Notice tone={c.status === 'escalated' ? 'warn' : 'info'}>{c.status_reason}</Notice>
          ) : null}
          <CaseFileView file={c.case_file} decision={c.decision} />
          <ol className="thread">
            {c.messages.map((m) =>
              m.direction === 'inbound' ? (
                <Inbound key={m.id} message={m} />
              ) : (
                <Outbound
                  key={`${m.id}-${m.status}`}
                  message={m}
                  email={email}
                  busy={busy !== null}
                  onAct={act}
                  onRedraft={(feedback) => void run('reply', (signal) => api.redraft(c.id, feedback, signal))}
                />
              ),
            )}
          </ol>
          {busy === 'reply' ? <AgentProgress steps={REPLY_STEPS} label="Writing the reply" /> : null}
          <FollowUpView
            followUps={c.follow_ups}
            busy={busy !== null}
            onRun={(id) => void run('reply', () => api.runFollowUp(id))}
          />
          {!closed ? (
            <form className="thread-reply" onSubmit={addReply}>
              <h3>The customer replied?</h3>
              <p className="muted small">
                Paste their email here until the mailbox is connected.
                {c.status === 'escalated' ? ' A person handles this conversation now, so no reply will be drafted.' : ''}
              </p>
              <label className="field" htmlFor={`${ids}-subject`}>
                Subject (optional)
                <input id={`${ids}-subject`} value={replySubject} onChange={(e) => setReplySubject(e.target.value)} />
              </label>
              <label className="field" htmlFor={`${ids}-body`}>
                Their email
                <textarea
                  id={`${ids}-body`}
                  rows={4}
                  maxLength={5000}
                  value={replyBody}
                  onChange={(e) => setReplyBody(e.target.value)}
                />
              </label>
              <div>
                <button type="submit" className="btn" disabled={busy !== null || !replyBody.trim()}>
                  Add the customer's reply
                </button>
              </div>
            </form>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
