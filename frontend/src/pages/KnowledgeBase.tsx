import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { KnowledgeBaseStats } from '../api/types'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { HealthList } from '../components/HealthList'
import { RecentActivity } from '../components/RecentActivity'
import {
  ErrorState,
  Loading,
  Notice,
  PageHeader,
  RefreshButton,
  StatCard,
  StatusBadge,
} from '../components/ui'
import { useResource } from '../hooks/useResource'
import { errorMessage, formatBytes, formatDateTime, formatNumber } from '../lib/format'

function statusNotice(stats: KnowledgeBaseStats) {
  switch (stats.status) {
    case 'empty':
      return (
        <Notice tone="info">
          The knowledge base is empty. <Link to="/assistant/documents">Upload a policy document</Link> to get
          started.
        </Notice>
      )
    case 'processing':
      return <Notice tone="info">Documents are being indexed. Figures update automatically.</Notice>
    case 'unavailable':
      return (
        <Notice tone="error">
          No chunks are searchable, so questions cannot be answered.{' '}
          {stats.stale_documents > 0
            ? 'The documents were indexed with a different embedding model: rebuild the index.'
            : 'Every document failed ingestion: review them on the Documents page.'}
        </Notice>
      )
    case 'degraded':
      return (
        <Notice tone="warn">
          {stats.by_status.failed > 0
            ? `${stats.by_status.failed} document${stats.by_status.failed === 1 ? '' : 's'} failed ingestion. `
            : ''}
          {stats.stale_documents > 0
            ? `${stats.stale_documents} document${stats.stale_documents === 1 ? ' was' : 's were'} indexed with a different embedding model and ${stats.stale_documents === 1 ? 'is' : 'are'} not searchable until the index is rebuilt.`
            : ''}
        </Notice>
      )
    default:
      return null
  }
}

export function KnowledgeBase() {
  const stats = useResource(api.stats, (s) => (s.status === 'processing' ? 1500 : null))
  const health = useResource(api.health)
  const config = useResource(api.config)
  const [confirming, setConfirming] = useState(false)
  const [rebuilding, setRebuilding] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)

  async function rebuild() {
    setRebuilding(true)
    try {
      const { queued } = await api.rebuild()
      setNotice({
        tone: 'ok',
        text: `Rebuild started for ${queued} document${queued === 1 ? '' : 's'}.`,
      })
    } catch (error) {
      setNotice({ tone: 'error', text: `Could not start the rebuild: ${errorMessage(error)}` })
    } finally {
      setRebuilding(false)
      setConfirming(false)
      await stats.refresh()
    }
  }

  const s = stats.data
  const c = config.data

  return (
    <>
      <PageHeader
        title="Knowledge Base"
        eyebrow="Retrieval index"
        description="What is indexed, how it was indexed, and whether the RAG service is healthy."
        actions={
          <>
            <RefreshButton
              busy={stats.refreshing || health.refreshing}
              onClick={() => {
                void stats.refresh()
                void health.refresh()
              }}
            />
            <button
              type="button"
              className="btn"
              onClick={() => setConfirming(true)}
              disabled={!s || s.total_documents === 0 || s.status === 'processing'}
            >
              Rebuild index
            </button>
          </>
        }
      />

      {notice ? (
        <Notice tone={notice.tone} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      ) : null}

      {stats.loading ? (
        <Loading label="Loading statistics…" />
      ) : stats.error && !s ? (
        <ErrorState error={stats.error} onRetry={stats.refresh} />
      ) : s ? (
        <>
          {statusNotice(s)}
          <section className="grid grid-4" aria-label="Index statistics">
            <StatCard label="Status" value={<StatusBadge status={s.status} />} />
            <StatCard
              label="Indexed documents"
              value={`${formatNumber(s.by_status.indexed)} / ${formatNumber(s.total_documents)}`}
              hint={`${s.by_status.failed} failed · ${s.by_status.pending + s.by_status.processing} in progress`}
            />
            <StatCard
              label="Searchable chunks"
              value={formatNumber(s.searchable_chunks)}
              hint={
                s.total_chunks !== s.searchable_chunks
                  ? `${formatNumber(s.total_chunks)} stored in total`
                  : 'All stored chunks are searchable'
              }
            />
            <StatCard label="Last indexed" value={<span className="stat-text">{formatDateTime(s.last_indexed_at)}</span>} />
          </section>

          <div className="grid grid-2">
            <section className="card" aria-labelledby="index-title">
              <h2 id="index-title">Index details</h2>
              <dl className="details">
                <div>
                  <dt>Indexed text</dt>
                  <dd>{formatNumber(s.indexed_characters)} characters</dd>
                </div>
                <div>
                  <dt>Stored files</dt>
                  <dd>{formatBytes(s.total_size_bytes)}</dd>
                </div>
                <div>
                  <dt>Documents by type</dt>
                  <dd>
                    {Object.keys(s.by_file_type).length
                      ? Object.entries(s.by_file_type)
                          .map(([type, count]) => `${count} ${type.toUpperCase()}`)
                          .join(' · ')
                      : '—'}
                  </dd>
                </div>
                <div>
                  <dt>Embedding model</dt>
                  <dd className="mono">{s.embedding_model}</dd>
                </div>
                <div>
                  <dt>Chunking</dt>
                  <dd>
                    {s.chunk_size} characters, {s.chunk_overlap} overlap
                  </dd>
                </div>
                <div>
                  <dt>Answer model</dt>
                  <dd className="mono">
                    {config.loading
                      ? '…'
                      : c
                        ? c.llm_model
                          ? [c.llm_provider, c.llm_model].filter(Boolean).join(' · ')
                          : 'Not configured (retrieval-only)'
                        : 'Unavailable'}
                  </dd>
                </div>
                <div>
                  <dt>Retrieval defaults</dt>
                  <dd>{c ? `Top ${c.default_top_k} chunks, minimum score ${c.default_min_score}` : config.loading ? '…' : 'Unavailable'}</dd>
                </div>
              </dl>
            </section>

            <section className="card" aria-labelledby="kb-health-title">
              <div className="card-head">
                <h2 id="kb-health-title">RAG service health</h2>
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
      ) : null}

      <RecentActivity />

      <ConfirmDialog
        open={confirming}
        title="Rebuild the whole index?"
        confirmLabel="Rebuild index"
        busy={rebuilding}
        onConfirm={() => void rebuild()}
        onCancel={() => setConfirming(false)}
      >
        <p>
          Every document is re-read from its stored file, re-chunked and re-embedded with the current
          settings. Documents are not searchable while they are queued. No files are deleted.
        </p>
      </ConfirmDialog>
    </>
  )
}
