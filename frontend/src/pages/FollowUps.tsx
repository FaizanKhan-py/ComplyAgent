import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { FollowUp } from '../api/types'
import { Badge, EmptyState, ErrorState, Loading, Notice, PageHeader } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { errorMessage, formatDateTime } from '../lib/format'

const FOLLOW_UP_ACTION: Record<FollowUp['action'], string> = {
  send_reminder: 'Send a reminder',
  schedule_call: 'Call the customer',
  escalate_to_human: 'Hand to a person',
}

const STATUS_TONE = { scheduled: 'warn', done: 'ok', cancelled: 'neutral' } as const

/**
 * What the Follow-up Agent planned after each email: wait, then remind, call or hand over.
 * A due reminder is drafted through the same compliance loop and waits for approval on the
 * customer's page.
 */
export function FollowUps() {
  const [status, setStatus] = useState<FollowUp['status'] | ''>('scheduled')
  const list = useResource(() => api.followUps(status || undefined))
  const [busy, setBusy] = useState(false)
  // When the list was loaded, so "Due now" does not change between renders.
  const [now, setNow] = useState(() => Date.now())
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error' | 'info'; text: string } | null>(null)
  const first = useRef(true)
  const { refresh } = list
  useEffect(() => {
    if (first.current) {
      first.current = false // the first load is useResource's own
      return
    }
    void refresh()
  }, [status, refresh])

  async function act(run: () => Promise<string>) {
    setBusy(true)
    setNotice(null)
    try {
      setNotice({ tone: 'ok', text: await run() })
      await list.refresh()
      setNow(Date.now())
    } catch (err) {
      setNotice({ tone: 'error', text: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Follow-up Agent"
        title="Follow-ups"
        description="After each email, the Follow-up Agent plans what happens if the customer does not reply, following the contact policy."
        actions={
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const r = await api.runDueFollowUps()
                return r.due === 0
                  ? 'Nothing is due yet.'
                  : `${r.done.length} of ${r.due} due follow-up(s) done${r.failed.length ? `; ${r.failed.length} failed: ${r.failed.map((f) => f.error).join('; ')}` : '.'}`
              })
            }
          >
            Run due follow-ups
          </button>
        }
      />
      {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}
      <section className="card" aria-label="Follow-ups">
        <div className="filters">
          <label className="field">
            <span>Show</span>
            <select value={status} onChange={(e) => setStatus(e.target.value as FollowUp['status'] | '')}>
              <option value="scheduled">Scheduled</option>
              <option value="done">Done</option>
              <option value="cancelled">Cancelled</option>
              <option value="">All</option>
            </select>
          </label>
        </div>
        {list.loading ? (
          <Loading label="Loading follow-ups…" />
        ) : list.error && !list.data ? (
          <ErrorState error={list.error} onRetry={list.refresh} />
        ) : !list.data?.length ? (
          <EmptyState title="No follow-ups here">
            Sending an approved email on a customer's page schedules the next follow-up.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Due</th>
                  <th scope="col">Customer</th>
                  <th scope="col">Plan</th>
                  <th scope="col">Why</th>
                  <th scope="col">Status</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((f) => {
                  const overdue = f.status === 'scheduled' && new Date(f.due_at).getTime() <= now
                  return (
                    <tr key={f.id}>
                      <td data-label="Due">
                        {formatDateTime(f.due_at)} {overdue ? <Badge tone="error">Due now</Badge> : null}
                      </td>
                      <td data-label="Customer">
                        <Link to={`/customers/${f.customer_id}`}>{f.customer_id}</Link>
                      </td>
                      <td data-label="Plan">{FOLLOW_UP_ACTION[f.action]}</td>
                      <td data-label="Why">
                        {f.reason}
                        {f.outcome ? <p className="muted small">{f.outcome}</p> : null}
                      </td>
                      <td data-label="Status">
                        <Badge tone={STATUS_TONE[f.status]}>{f.status}</Badge>
                      </td>
                      <td data-label="Actions">
                        {f.status === 'scheduled' ? (
                          <button
                            type="button"
                            className="btn btn-sm"
                            disabled={busy}
                            onClick={() =>
                              void act(async () => {
                                await api.runFollowUp(f.id)
                                return `Follow-up for customer ${f.customer_id} done. Open the customer to review it.`
                              })
                            }
                          >
                            Run now
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  )
}
