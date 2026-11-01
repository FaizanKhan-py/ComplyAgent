import type { ReactNode } from 'react'
import { useCountUp } from '../lib/motion'
import type { ComponentStatus, DocumentStatus, KnowledgeBaseStatus } from '../api/types'

type Tone = 'ok' | 'warn' | 'error' | 'info' | 'neutral'

const STATUS_TONE: Record<string, Tone> = {
  indexed: 'ok',
  ready: 'ok',
  ok: 'ok',
  pending: 'info',
  processing: 'info',
  degraded: 'warn',
  failed: 'error',
  error: 'error',
  unavailable: 'error',
  empty: 'neutral',
}

const STATUS_LABEL: Record<string, string> = {
  indexed: 'Indexed',
  pending: 'Queued',
  processing: 'Processing',
  failed: 'Failed',
  ready: 'Ready',
  degraded: 'Degraded',
  unavailable: 'Unavailable',
  empty: 'Empty',
  ok: 'Healthy',
  error: 'Down',
}

export function StatusBadge({
  status,
}: {
  status: DocumentStatus | ComponentStatus | KnowledgeBaseStatus
}) {
  const busy = status === 'pending' || status === 'processing'
  return (
    <span className={`badge badge-${STATUS_TONE[status] ?? 'neutral'}`}>
      {busy ? <span className="spinner spinner-sm" aria-hidden="true" /> : <span className="dot" aria-hidden="true" />}
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function StatCard({
  label,
  value,
  hint,
  tone = 'neutral',
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: Tone
}) {
  return (
    <div className={`card stat stat-${tone}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{typeof value === 'string' ? <CountUp value={value} /> : value}</div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </div>
  )
}

/** A figure that counts up when it first appears; screen readers get the final value only. */
function CountUp({ value }: { value: string }) {
  const shown = useCountUp(value)
  if (shown === value) return <>{value}</>
  return (
    <>
      <span aria-hidden="true">{shown}</span>
      <span className="visually-hidden">{value}</span>
    </>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <strong>Could not load this data</strong>
      <span>{error.message}</span>
      {onRetry ? (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  )
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="state">
      <strong>{title}</strong>
      {children ? <span>{children}</span> : null}
      {action}
    </div>
  )
}

export function Notice({
  tone,
  children,
  onDismiss,
}: {
  tone: 'ok' | 'warn' | 'error' | 'info'
  children: ReactNode
  onDismiss?: () => void
}) {
  return (
    <div className={`notice notice-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <div>{children}</div>
      {onDismiss ? (
        <button type="button" className="btn btn-ghost btn-sm" onClick={onDismiss} aria-label="Dismiss">
          ✕
        </button>
      ) : null}
    </div>
  )
}

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: string
  description: string
  actions?: ReactNode
  /** A short kicker above the title naming the part of the product, e.g. "Portfolio ledger". */
  eyebrow?: string
}) {
  return (
    <header className="page-header">
      <div className="page-title">
        {eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  )
}

export function RefreshButton({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  return (
    <button type="button" className="btn" onClick={onClick} disabled={busy}>
      {busy ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
      Refresh
    </button>
  )
}
