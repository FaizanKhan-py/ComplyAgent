import type { RiskLevel } from '../api/types'
import { formatPercent } from '../lib/format'
import { RISK_LABEL, repaymentStatus } from '../lib/risk'
import { Badge } from './ui'

const RISK_TONE = { low: 'ok', medium: 'warn', high: 'error' } as const

/** The model's prediction: the chance of default and the level it falls in. */
export function RiskBadge({ level, probability }: { level: RiskLevel | null; probability: number | null }) {
  if (level === null || probability === null) return <span className="muted">Not scored</span>
  return (
    <Badge tone={RISK_TONE[level]}>
      {RISK_LABEL[level]} · {formatPercent(probability)}
    </Badge>
  )
}

/** What actually happened the following month. A fact from the data, not a prediction. */
export function ActualBadge({ defaulted }: { defaulted: boolean }) {
  return <Badge tone={defaulted ? 'error' : 'ok'}>{defaulted ? 'Defaulted' : 'Did not default'}</Badge>
}

/** How one month's bill was paid. */
export function PaymentBadge({ status }: { status: number | null }) {
  if (status === null) return <span className="muted">—</span>
  const tone = status >= 2 ? 'error' : status === 1 ? 'warn' : status === -1 ? 'ok' : 'neutral'
  return <Badge tone={tone}>{repaymentStatus(status)}</Badge>
}
