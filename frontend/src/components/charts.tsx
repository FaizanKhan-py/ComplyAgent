import type { ReactNode } from 'react'

export interface Bar {
  label: string
  /** Bar length as a share of the track, 0 to 1. */
  share: number
  value: ReactNode
  /** Shown on hover, and read out with the row. */
  detail?: string
}

/** Horizontal bars, one row per category, each with its value written beside it. */
export function BarList({ bars, labelledBy }: { bars: Bar[]; labelledBy: string }) {
  return (
    <ul className="bars" aria-labelledby={labelledBy}>
      {bars.map((bar) => (
        <li key={bar.label} title={bar.detail}>
          <span className="bar-label">{bar.label}</span>
          <span className="bar-track" aria-hidden="true">
            <span className="bar-fill" style={{ width: `${clamp(bar.share) * 100}%` }} />
          </span>
          <span className="bar-value">{bar.value}</span>
          {bar.detail ? <span className="visually-hidden">{bar.detail}</span> : null}
        </li>
      ))}
    </ul>
  )
}

/** Vertical bars for an ordered series such as years. */
export function ColumnChart({ bars, labelledBy }: { bars: Bar[]; labelledBy: string }) {
  return (
    <div className="columns-wrap">
      <ol className="columns" aria-labelledby={labelledBy}>
        {bars.map((bar) => (
          <li key={bar.label} title={bar.detail}>
            <span className="column-value">{bar.value}</span>
            <span className="column-track" aria-hidden="true">
              <span className="column-fill" style={{ height: `${clamp(bar.share) * 100}%` }} />
            </span>
            <span className="column-label">{bar.label}</span>
            {bar.detail ? <span className="visually-hidden">{bar.detail}</span> : null}
          </li>
        ))}
      </ol>
    </div>
  )
}

function clamp(share: number): number {
  return Number.isFinite(share) ? Math.max(0, Math.min(1, share)) : 0
}
