import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { FollowUp, InboxDraft } from '../api/types'
import { Badge, EmptyState, ErrorState, Loading, PageHeader, RefreshButton } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { formatDateTime } from '../lib/format'

const DRAFT_LABEL: Record<InboxDraft['status'], { label: string; tone: 'warn' | 'error' | 'ok' }> = {
  pending_approval: { label: 'Waiting for approval', tone: 'warn' },
  blocked: { label: 'Blocked by the Red Team', tone: 'error' },
  approved: { label: 'Approved, not sent', tone: 'ok' },
}

function excerpt(text: string, words = 22): string {
  const parts = text.split(/\s+/)
  return parts.length > words ? `${parts.slice(0, words).join(' ')}…` : text
}

function Section({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <section className="card" aria-label={title}>
      <div className="card-head">
        <h2>
          {title} <Badge tone={count ? 'warn' : 'neutral'}>{count}</Badge>
        </h2>
      </div>
      {count ? <ul className="inbox-list">{children}</ul> : <p className="muted">Nothing here.</p>}
    </section>
  )
}

function FollowUpItem({ f, label }: { f: FollowUp; label: string }) {
  return (
    <li>
      <Link to={`/customers/${f.customer_id}`}>Customer {f.customer_id}</Link> · {label}
      <p className="muted small">
        {f.outcome ?? f.reason} ({formatDateTime(f.done_at ?? f.due_at)})
      </p>
    </li>
  )
}

/**
 * Everything across customers that waits for an officer, oldest first: drafts to approve,
 * blocked drafts, conversations handed to a person, follow-ups that are due and calls to make.
 */
export function Inbox() {
  const inbox = useResource(api.inbox, 30000)
  const data = inbox.data
  return (
    <>
      <PageHeader
        eyebrow="Human approval"
        title="Approvals"
        description="Nothing reaches a customer until an officer approves it. Everything waiting for a person, oldest first."
        actions={<RefreshButton busy={inbox.refreshing} onClick={() => void inbox.refresh()} />}
      />
      {inbox.loading ? (
        <Loading label="Loading the inbox…" />
      ) : inbox.error && !data ? (
        <ErrorState error={inbox.error} onRetry={inbox.refresh} />
      ) : data && data.total === 0 ? (
        <section className="card">
          <EmptyState title="All clear">No draft, hand-over or follow-up is waiting for an officer.</EmptyState>
        </section>
      ) : data ? (
        <>
          <Section title="Drafts" count={data.drafts.length}>
            {data.drafts.map((d) => (
              <li key={d.id}>
                <Link to={`/customers/${d.customer_id}`}>Customer {d.customer_id}</Link>{' '}
                <Badge tone={DRAFT_LABEL[d.status].tone}>{DRAFT_LABEL[d.status].label}</Badge>
                <p className="small">
                  {d.subject ? <strong>{d.subject}: </strong> : null}
                  {excerpt(d.body)}
                </p>
                <p className="muted small">Written {formatDateTime(d.created_at)}</p>
              </li>
            ))}
          </Section>
          <Section title="Handed to a person" count={data.escalated.length}>
            {data.escalated.map((e) => (
              <li key={e.id}>
                <Link to={`/customers/${e.customer_id}`}>Customer {e.customer_id}</Link>
                <p className="muted small">
                  {e.status_reason} ({formatDateTime(e.updated_at)})
                </p>
              </li>
            ))}
          </Section>
          <Section title="Calls to make" count={data.calls.length}>
            {data.calls.map((f) => (
              <FollowUpItem key={f.id} f={f} label="call the customer" />
            ))}
          </Section>
          <Section title="Follow-ups due" count={data.due_follow_ups.length}>
            {data.due_follow_ups.map((f) => (
              <FollowUpItem key={f.id} f={f} label={`due: ${f.action.replace(/_/g, ' ')}`} />
            ))}
          </Section>
        </>
      ) : null}
    </>
  )
}
