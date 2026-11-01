import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { CollectionsDashboard as Summary } from '../api/types'
import { BarList, ColumnChart } from '../components/charts'
import { ErrorState, Loading, PageHeader, RefreshButton, StatCard } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { formatDateTime, formatMoney, formatPercent } from '../lib/format'

const INTENT_LABEL: Record<string, string> = {
  promise_to_pay: 'Promise to pay',
  payment_made: 'Says they paid',
  hardship: 'Hardship',
  dispute: 'Dispute',
  question: 'Question',
  callback_request: 'Wants a call',
  stop_contact: 'Stop contact',
  complaint: 'Complaint',
  other: 'Other',
}

const rate = (value: number | null) => (value === null ? '—' : formatPercent(value))

function Body({ s }: { s: Summary }) {
  const waiting = s.work.pending_approval + s.work.awaiting_supervisor
  const days = s.activity.sent_by_day
  const busiest = Math.max(1, ...days.map((d) => d.emails))
  const intents = Object.entries(s.activity.intents)
  const mostIntents = Math.max(1, ...intents.map(([, n]) => n))
  return (
    <>
      <section className="grid grid-4" aria-label="Work waiting for a person">
        <StatCard
          label="Waiting for approval"
          value={String(waiting)}
          tone={waiting ? 'warn' : 'neutral'}
          hint={
            <Link to="/approvals">
              {s.work.awaiting_supervisor} need a team lead · open Approvals
            </Link>
          }
        />
        <StatCard
          label="With a person"
          value={String(s.work.escalated)}
          tone={s.work.escalated ? 'error' : 'neutral'}
          hint="Conversations the agents handed over"
        />
        <StatCard
          label="Blocked by the Red Team"
          value={String(s.work.blocked)}
          tone={s.work.blocked ? 'error' : 'neutral'}
          hint="Drafts that must be written again"
        />
        <StatCard
          label="Follow-ups due"
          value={String(s.work.follow_ups_due)}
          tone={s.work.follow_ups_due ? 'warn' : 'neutral'}
          hint={<Link to="/follow-ups">{s.activity.follow_ups_scheduled} scheduled in all</Link>}
        />
      </section>

      <section className="grid grid-4" aria-label="Results">
        <StatCard
          label="Promises kept"
          value={rate(s.promises.kept_rate)}
          hint={`${s.promises.kept} kept, ${s.promises.broken} broken, ${s.promises.open} open`}
        />
        <StatCard
          label="Emails per resolution"
          value={s.conversations.emails_per_resolution === null ? '—' : s.conversations.emails_per_resolution.toFixed(1)}
          hint={`${s.conversations.closed} conversation(s) closed`}
        />
        <StatCard
          label="Collected"
          value={formatMoney(s.promises.collected)}
          hint="Payments recorded in the ledger"
        />
        <StatCard
          label="Passed the Red Team first time"
          value={rate(s.compliance.first_round_pass_rate)}
          hint={
            s.compliance.average_rounds === null
              ? 'No drafts reviewed yet'
              : `${s.compliance.average_rounds.toFixed(1)} rounds on average, ${s.compliance.drafts_reviewed} drafts`
          }
        />
      </section>

      <div className="grid grid-split">
        <section className="card" aria-labelledby="sent-days">
          <h2 id="sent-days">Emails sent, last 14 days</h2>
          <p className="muted small">
            {s.activity.emails_sent} sent in all, {s.activity.emails_delivered} delivered through Microsoft 365.
          </p>
          <ColumnChart
            labelledBy="sent-days"
            bars={days.map((d) => ({
              label: d.day.slice(5),
              share: d.emails / busiest,
              value: d.emails,
              detail: `${d.day}: ${d.emails} email(s)`,
            }))}
          />
        </section>
        <section className="card" aria-labelledby="intents">
          <h2 id="intents">What customers wrote about</h2>
          {intents.length ? (
            <BarList
              labelledBy="intents"
              bars={intents.map(([intent, n]) => ({
                label: INTENT_LABEL[intent] ?? intent,
                share: n / mostIntents,
                value: n,
                tone: intent === 'dispute' || intent === 'complaint' || intent === 'stop_contact' ? 'high' : undefined,
              }))}
            />
          ) : (
            <p className="muted">No customer replies yet.</p>
          )}
        </section>
      </div>

      <div className="grid grid-2">
        <section className="card" aria-labelledby="compliance">
          <h2 id="compliance">Compliance</h2>
          <dl className="details">
            <div>
              <dt>Sends refused by the contact rules</dt>
              <dd>{s.compliance.sends_refused}</dd>
            </div>
            <div>
              <dt>Customers who asked for no contact</dt>
              <dd>{s.compliance.do_not_contact}</dd>
            </div>
            <div>
              <dt>Open conversations</dt>
              <dd>{s.conversations.open}</dd>
            </div>
          </dl>
          <p className="muted small">
            <Link to="/audit">Every step is in the Audit Trail</Link>, which can be exported as CSV.
          </p>
        </section>
        <section className="card" aria-labelledby="escalations">
          <h2 id="escalations">Latest hand-overs</h2>
          {s.conversations.recent_escalations.length ? (
            <ul className="inbox-list">
              {s.conversations.recent_escalations.map((e) => (
                <li key={e.id}>
                  <Link to={`/customers/${e.customer_id}`}>Customer {e.customer_id}</Link>
                  <p className="muted small">
                    {e.status_reason} ({formatDateTime(e.updated_at)})
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No conversation is with a person.</p>
          )}
        </section>
      </div>
    </>
  )
}

/** How collections are going: the work waiting for people, results, and compliance. */
export function CollectionsDashboard() {
  const summary = useResource(api.collectionsSummary, 60000)
  return (
    <>
      <PageHeader
        eyebrow="Collections desk"
        title="Collections"
        description="The work waiting for people, what the conversations achieved, and how the compliance checks are doing."
        actions={<RefreshButton busy={summary.refreshing} onClick={() => void summary.refresh()} />}
      />
      {summary.loading ? (
        <Loading label="Loading the collections figures…" />
      ) : summary.error && !summary.data ? (
        <ErrorState error={summary.error} onRetry={summary.refresh} />
      ) : summary.data ? (
        <Body s={summary.data} />
      ) : null}
    </>
  )
}
