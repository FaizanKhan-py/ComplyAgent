import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ActivityEvent } from '../api/types'
import { HealthList } from '../components/HealthList'
import {
  EmptyState,
  ErrorState,
  Loading,
  PageHeader,
  RefreshButton,
  StatCard,
  StatusBadge,
} from '../components/ui'
import { useResource } from '../hooks/useResource'
import { formatDateTime, formatNumber } from '../lib/format'

const KB_HINT = {
  empty: 'No documents uploaded yet',
  processing: 'Documents are being indexed',
  ready: 'All documents are searchable',
  degraded: 'Some documents need attention',
  unavailable: 'Nothing is searchable right now',
}

const EVENT_LABEL: Record<ActivityEvent['event'], string> = {
  uploaded: 'Uploaded',
  indexed: 'Indexed',
  failed: 'Ingestion failed',
  reindex: 'Reindex requested',
  deleted: 'Deleted',
}

const EVENT_TONE: Record<ActivityEvent['event'], string> = {
  uploaded: 'info',
  indexed: 'ok',
  failed: 'error',
  reindex: 'info',
  deleted: 'neutral',
}

export function Dashboard() {
  const stats = useResource(api.stats, (s) => (s.status === 'processing' ? 2000 : 15000))
  const activity = useResource(() => api.activity(8), 5000)
  const health = useResource(api.health, 15000)

  const refreshAll = () => {
    void stats.refresh()
    void activity.refresh()
    void health.refresh()
  }
  const s = stats.data
  const inProgress = s ? s.by_status.pending + s.by_status.processing : 0

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="The state of the policy knowledge base at a glance."
        actions={
          <RefreshButton
            busy={stats.refreshing || activity.refreshing || health.refreshing}
            onClick={refreshAll}
          />
        }
      />

      {stats.loading ? (
        <Loading label="Loading metrics…" />
      ) : stats.error && !s ? (
        <ErrorState error={stats.error} onRetry={stats.refresh} />
      ) : s ? (
        <section className="grid grid-4" aria-label="Overview">
          <StatCard
            label="Total documents"
            value={formatNumber(s.total_documents)}
            hint={inProgress ? `${inProgress} in progress` : 'Uploaded policy files'}
          />
          <StatCard
            label="Indexed"
            value={formatNumber(s.by_status.indexed)}
            tone={s.by_status.indexed ? 'ok' : 'neutral'}
            hint={`${formatNumber(s.searchable_chunks)} searchable chunks`}
          />
          <StatCard
            label="Failed"
            value={formatNumber(s.by_status.failed)}
            tone={s.by_status.failed ? 'error' : 'neutral'}
            hint={
              s.by_status.failed ? <Link to="/documents">Review failed documents</Link> : 'No failures'
            }
          />
          <StatCard
            label="Knowledge base"
            value={<StatusBadge status={s.status} />}
            hint={KB_HINT[s.status]}
          />
        </section>
      ) : null}

      <div className="grid grid-2">
        <section className="card" aria-labelledby="activity-title">
          <div className="card-head">
            <h2 id="activity-title">Recent activity</h2>
            <Link to="/documents">All documents</Link>
          </div>
          {activity.loading ? (
            <Loading />
          ) : activity.error && !activity.data ? (
            <ErrorState error={activity.error} onRetry={activity.refresh} />
          ) : activity.data && activity.data.length > 0 ? (
            <ul className="activity">
              {activity.data.map((event) => (
                <li key={event.id}>
                  <span className={`dot dot-${EVENT_TONE[event.event]}`} aria-hidden="true" />
                  <div className="activity-body">
                    <div>
                      <strong>{EVENT_LABEL[event.event]}</strong>{' '}
                      <span className="filename">{event.filename}</span>
                    </div>
                    {event.detail ? <div className="muted small">{event.detail}</div> : null}
                  </div>
                  <time className="muted small" dateTime={event.created_at}>
                    {formatDateTime(event.created_at)}
                  </time>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No activity yet"
              action={
                <Link className="btn btn-primary" to="/documents">
                  Upload a document
                </Link>
              }
            >
              Uploads, indexing results and deletions will appear here.
            </EmptyState>
          )}
        </section>

        <section className="card" aria-labelledby="health-title">
          <div className="card-head">
            <h2 id="health-title">Service health</h2>
            {health.data ? <StatusBadge status={health.data.status} /> : null}
          </div>
          {health.loading ? (
            <Loading />
          ) : health.error && !health.data ? (
            <ErrorState error={health.error} onRetry={health.refresh} />
          ) : health.data ? (
            <HealthList health={health.data} />
          ) : null}
        </section>
      </div>
    </>
  )
}
