import type { RiskLevel } from '../api/types'
import { formatNumber, formatPercent } from '../lib/format'
import { RISK_LABEL, repaymentStatus } from '../lib/risk'

const ORDER: RiskLevel[] = ['low', 'medium', 'high']

/**
 * The whole portfolio as one band, split by predicted risk, with each share written out below.
 * The band is decoration; the legend carries the numbers.
 */
export function RiskSpectrum({ counts }: { counts: Partial<Record<RiskLevel, number>> }) {
  const total = ORDER.reduce((sum, level) => sum + (counts[level] ?? 0), 0)
  return (
    <div className="spectrum">
      <div className="spectrum-band" aria-hidden="true">
        {ORDER.map((level) => (
          <span
            key={level}
            className={`spectrum-seg seg-${level}`}
            style={{ flexGrow: counts[level] ?? 0 }}
          />
        ))}
      </div>
      <div className="spectrum-scale" aria-hidden="true">
        <span>0%</span>
        <span>Predicted chance of default rises →</span>
        <span>100%</span>
      </div>
      <ul className="spectrum-legend">
        {ORDER.map((level) => {
          const count = counts[level] ?? 0
          return (
            <li key={level}>
              <span className={`swatch seg-${level}`} aria-hidden="true" />
              <span className="spectrum-label">{RISK_LABEL[level]}</span>
              <strong className="spectrum-count">{formatNumber(count)}</strong>
              <span className="spectrum-share">{total ? formatPercent(count / total, 1) : '—'}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/** A half-dial showing one probability, with the 40% and 70% risk thresholds marked. */
export function ProbabilityGauge({ probability, level }: { probability: number; level: RiskLevel }) {
  const share = Math.max(0, Math.min(1, probability))
  const percent = Math.round(share * 100)
  // Points on the arc for the thresholds, so the bands line up with RISK_RANGE.
  const tick = (p: number) => {
    const angle = Math.PI * (1 - p)
    return { x: 100 + 82 * Math.cos(angle), y: 100 - 82 * Math.sin(angle), x2: 100 + 94 * Math.cos(angle), y2: 100 - 94 * Math.sin(angle) }
  }
  return (
    <div className={`gauge gauge-${level}`} aria-hidden="true">
      <svg viewBox="0 0 200 112">
        <path className="gauge-track" d="M12 100a88 88 0 0 1 176 0" pathLength={100} />
        <path
          className="gauge-value"
          d="M12 100a88 88 0 0 1 176 0"
          pathLength={100}
          style={{ strokeDasharray: `${percent} 100` }}
        />
        {[0.4, 0.7].map((p) => {
          const t = tick(p)
          return <line key={p} className="gauge-tick" x1={t.x} y1={t.y} x2={t.x2} y2={t.y2} />
        })}
      </svg>
      <div className="gauge-readout">
        <span className="gauge-number">
          {percent}
          <small>%</small>
        </span>
        <span className="gauge-caption">chance of default</span>
      </div>
      <span className="stamp">{RISK_LABEL[level]}</span>
    </div>
  )
}

/** Six months of repayment at a glance, one tile per month. The table below has the detail. */
export function PaymentStrip({ months }: { months: { month: string; status: number | null }[] }) {
  return (
    <ol className="paystrip" aria-hidden="true">
      {months.map((m) => (
        <li key={m.month} title={`${m.month}: ${repaymentStatus(m.status)}`}>
          <span className={`paytile ${payTone(m.status)}`} />
          <span className="paystrip-label">{m.month.split(' ')[0]}</span>
        </li>
      ))}
    </ol>
  )
}

function payTone(status: number | null): string {
  if (status === null || status <= -2) return 'pay-idle'
  if (status === -1) return 'pay-full'
  if (status === 0) return 'pay-min'
  if (status === 1) return 'pay-late'
  return 'pay-overdue'
}
