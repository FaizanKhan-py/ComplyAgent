import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { AuditEvent } from '../api/types'
import { Badge, EmptyState, ErrorState, Loading, PageHeader } from '../components/ui'
import { errorMessage, formatDateTime } from '../lib/format'

const EVENT_GROUPS = [
  { value: '', label: 'Everything' },
  { value: 'agent', label: 'Agent steps' },
  { value: 'draft', label: 'Drafts and approvals' },
  { value: 'conversation', label: 'Conversations' },
  { value: 'follow_up', label: 'Follow-ups' },
  { value: 'simulation', label: 'Simulations' },
]

const PAGE = 50

function tone(event: string): 'ok' | 'warn' | 'error' | 'info' | 'neutral' {
  if (event === 'draft.blocked' || event === 'draft.rejected' || event === 'conversation.escalated') return 'error'
  if (event === 'draft.approved' || event === 'draft.sent') return 'ok'
  if (event.startsWith('agent.')) return 'info'
  if (event.startsWith('follow_up')) return 'warn'
  return 'neutral'
}

function isPerson(actor: string): boolean {
  return actor.includes('@') || actor === 'API token'
}

/**
 * Every agent step and every officer action, newest first. The log is append-only on the
 * server: rows can be read here, never changed.
 */
export function AuditTrail() {
  const [customer, setCustomer] = useState('')
  const [group, setGroup] = useState('')
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [next, setNext] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const customerId = /^\d+$/.test(customer.trim()) ? Number(customer.trim()) : undefined

  useEffect(() => {
    let alive = true
    api.audit({ customer_id: customerId, event: group || undefined, limit: PAGE }).then(
      (page) => {
        if (!alive) return
        setEvents(page.events)
        setNext(page.next_before)
        setError(null)
        setLoading(false)
      },
      (err: unknown) => {
        if (!alive) return
        setError(err instanceof Error ? err : new Error(errorMessage(err)))
        setLoading(false)
      },
    )
    return () => {
      alive = false
    }
  }, [customerId, group])

  async function more() {
    if (next === null) return
    const page = await api.audit({ customer_id: customerId, event: group || undefined, limit: PAGE, before: next })
    setEvents((current) => [...current, ...page.events])
    setNext(page.next_before)
  }

  return (
    <>
      <PageHeader
        eyebrow="Compliance record"
        title="Audit Trail"
        description="Every agent step and every officer action, newest first. Entries cannot be changed or deleted."
      />
      <section className="card" aria-label="Audit events">
        <div className="filters">
          <label className="field">
            <span>Customer</span>
            <input
              type="search"
              inputMode="numeric"
              maxLength={12}
              placeholder="Customer number"
              value={customer}
              onChange={(e) => setCustomer(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Show</span>
            <select value={group} onChange={(e) => setGroup(e.target.value)}>
              {EVENT_GROUPS.map((g) => (
                <option key={g.value} value={g.value}>
                  {g.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        {loading ? (
          <Loading label="Loading the audit trail…" />
        ) : error ? (
          <ErrorState error={error} />
        ) : events.length === 0 ? (
          <EmptyState title="Nothing recorded yet">
            Starting a conversation on a customer's page records every agent step here.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Who</th>
                  <th scope="col">Event</th>
                  <th scope="col">Customer</th>
                  <th scope="col">What happened</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id}>
                    <td data-label="When">{formatDateTime(e.created_at)}</td>
                    <td data-label="Who">
                      {isPerson(e.actor) ? e.actor : <Badge tone="info">{e.actor}</Badge>}
                    </td>
                    <td data-label="Event">
                      <Badge tone={tone(e.event)}>{e.event}</Badge>
                    </td>
                    <td data-label="Customer">
                      {e.customer_id !== null ? <Link to={`/customers/${e.customer_id}`}>{e.customer_id}</Link> : '—'}
                    </td>
                    <td data-label="What happened">
                      {e.summary}
                      {e.detail ? (
                        <>
                          {' '}
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            aria-expanded={open === e.id}
                            onClick={() => setOpen(open === e.id ? null : e.id)}
                          >
                            {open === e.id ? 'Hide details' : 'Details'}
                          </button>
                          {open === e.id ? <pre className="audit-detail">{JSON.stringify(e.detail, null, 2)}</pre> : null}
                        </>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {next !== null ? (
          <div>
            <button type="button" className="btn" onClick={() => void more()}>
              Show older
            </button>
          </div>
        ) : null}
      </section>
    </>
  )
}
