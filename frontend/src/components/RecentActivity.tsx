import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ActivityEvent } from '../api/types'
import { useResource } from '../hooks/useResource'
import { formatDateTime } from '../lib/format'
import { EmptyState, ErrorState, Loading } from './ui'

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

export function RecentActivity() {
  const activity = useResource(() => api.activity(8), 5000)

  return (
    <section className="card" aria-labelledby="activity-title">
      <div className="card-head">
        <h2 id="activity-title">Recent activity</h2>
        <Link to="/assistant/documents">All documents</Link>
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
        <EmptyState title="No activity yet">
          Uploads, indexing results and deletions will appear here.
        </EmptyState>
      )}
    </section>
  )
}
