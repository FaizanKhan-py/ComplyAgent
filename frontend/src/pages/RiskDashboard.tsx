import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { PortfolioSummary, RiskModel } from '../api/types'
import { BarList, ColumnChart } from '../components/charts'
import { RiskSpectrum } from '../components/visuals'
import { EmptyState, ErrorState, Loading, PageHeader, RefreshButton, StatCard } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { formatDateTime, formatMoneyCompact, formatNumber, formatPercent } from '../lib/format'
import { RISK_LABEL, RISK_RANGE } from '../lib/risk'

const people = (count: number) => `${formatNumber(count)} customer${count === 1 ? '' : 's'}`

function ModelCard({ model }: { model: RiskModel }) {
  const test = model.metrics.test
  return (
    <section className="card" aria-labelledby="model-title">
      <div>
        <h2 id="model-title">Prediction model</h2>
        <p className="muted small">
          Scores on the {people(test.customers)} held back for testing, which the model never saw in
          training.
        </p>
      </div>
      <dl className="details details-metrics">
        <div>
          <dt>Model</dt>
          <dd>{model.model_name}</dd>
        </div>
        <div>
          <dt>ROC-AUC</dt>
          <dd>{test.roc_auc.toFixed(3)}</dd>
        </div>
        <div>
          <dt>Recall</dt>
          <dd>
            {formatPercent(test.recall)} of the customers who defaulted were flagged ({formatNumber(test.tp)}{' '}
            of {formatNumber(test.tp + test.fn)})
          </dd>
        </div>
        <div>
          <dt>Precision</dt>
          <dd>
            {formatPercent(test.precision)} of the customers flagged did default ({formatNumber(test.tp)} of{' '}
            {formatNumber(test.tp + test.fp)})
          </dd>
        </div>
        <div>
          <dt>Accuracy</dt>
          <dd>{formatPercent(test.accuracy)}</dd>
        </div>
        <div>
          <dt>Trained</dt>
          <dd>{formatDateTime(model.trained_at)}</dd>
        </div>
      </dl>
    </section>
  )
}

function Charts({ s }: { s: PortfolioSummary }) {
  const high = s.by_risk.find((g) => g.risk_level === 'high')?.customers ?? 0
  const latestMonth = s.by_month.at(-1)?.month

  return (
    <>
      <section className="grid grid-4" aria-label="Portfolio overview">
        <StatCard
          label="Customers"
          value={formatNumber(s.customers)}
          hint={`${formatMoneyCompact(s.total_credit_limit)} total credit limit`}
        />
        <StatCard
          label="Paying late"
          value={s.latest_late_rate === null ? '—' : formatPercent(s.latest_late_rate, 1)}
          hint={latestMonth ? `At least one month late in ${latestMonth}` : undefined}
        />
        <StatCard
          label="High risk"
          value={s.model ? formatNumber(high) : '—'}
          tone={s.model && high ? 'error' : 'neutral'}
          hint={
            s.model ? (
              <Link to="/customers?risk=high&sort=probability&order=desc">
                {formatPercent(high / s.customers, 1)} of customers · view them
              </Link>
            ) : (
              'No model has been trained yet'
            )
          }
        />
        <StatCard
          label="Actual defaults"
          value={formatNumber(s.actual_defaults)}
          hint={`${formatPercent(s.actual_defaults / s.customers, 1)} of customers defaulted the next month`}
        />
      </section>

      {s.model ? (
        <div className="grid grid-split">
          <section className="card card-feature" aria-label="Customers by predicted risk">
            <div>
              <h2 id="risk-title">Customers by predicted risk</h2>
              <p className="muted small">
                The model's chance of default next month, in three levels: low {RISK_RANGE.low}, medium{' '}
                {RISK_RANGE.medium}, high {RISK_RANGE.high}.
              </p>
            </div>
            <RiskSpectrum
              counts={Object.fromEntries(s.by_risk.map((g) => [g.risk_level, g.customers]))}
            />
          </section>

          <section className="card" aria-label="Actual default rate by predicted risk">
            <div>
              <h2 id="actual-title">Did the levels hold?</h2>
              <p className="muted small">
                Actual default rate by predicted risk. Most of these customers were also used to train the
                model, so see the test scores below for unseen customers.
              </p>
            </div>
            <BarList
              labelledBy="actual-title"
              bars={s.by_risk.map((g) => ({
                label: RISK_LABEL[g.risk_level],
                share: g.default_rate ?? 0,
                value: g.default_rate === null ? '—' : formatPercent(g.default_rate),
                detail: people(g.customers),
                tone: g.risk_level,
              }))}
            />
          </section>
        </div>
      ) : (
        <section className="card">
          <EmptyState title="No risk predictions yet">
            <>
              Train the model and score every customer from the server, in the <code>backend</code>{' '}
              folder:
            </>
            <code>python -m app.manage score-customers</code>
          </EmptyState>
        </section>
      )}

      <div className="grid grid-split-rev">
        <section className="card" aria-label="Default rate by education">
          <div>
            <h2 id="education-title">Default rate by education</h2>
            <p className="muted small">Share of customers in each group who actually defaulted.</p>
          </div>
          <BarList
            labelledBy="education-title"
            bars={s.by_education.map((g) => ({
              label: g.education,
              share: g.default_rate,
              value: formatPercent(g.default_rate),
              detail: people(g.customers),
            }))}
          />
        </section>

        <section className="card" aria-label="Delinquency trend">
          <div className="card-head">
            <div>
              <h2 id="trend-title">Delinquency trend</h2>
              <p className="muted small">Share of customers at least one month late, by month.</p>
            </div>
            {latestMonth ? <span className="legend-key">Latest · {latestMonth}</span> : null}
          </div>
          <ColumnChart
            labelledBy="trend-title"
            bars={s.by_month.map((m) => ({
              label: m.month,
              share: m.late_rate * 2.5, // the scale tops out at 40% so the change is visible
              value: formatPercent(m.late_rate, 1),
              detail: `${formatMoneyCompact(m.billed)} billed, ${formatMoneyCompact(m.paid)} paid`,
              tone: m.month === latestMonth ? ('highlight' as const) : undefined,
            }))}
          />
        </section>
      </div>

      {s.model ? <ModelCard model={s.model} /> : null}
    </>
  )
}

export function RiskDashboard() {
  const summary = useResource(api.portfolioSummary)
  const s = summary.data

  return (
    <>
      <PageHeader
        title="Risk Dashboard"
        eyebrow="Portfolio ledger"
        description="Portfolio overview, risk distribution and delinquency trends."
        actions={<RefreshButton busy={summary.refreshing} onClick={summary.refresh} />}
      />
      {summary.loading ? (
        <Loading label="Loading the portfolio…" />
      ) : summary.error && !s ? (
        <ErrorState error={summary.error} onRetry={summary.refresh} />
      ) : s && !s.loaded ? (
        <section className="card">
          <NoCustomerData />
        </section>
      ) : s ? (
        <Charts s={s} />
      ) : null}
    </>
  )
}

export function NoCustomerData() {
  return (
    <EmptyState title="No customer data loaded yet">
      <>
        Load the customers from the server, in the <code>backend</code> folder:
      </>
      <code>python -m app.manage import-customers</code>
    </EmptyState>
  )
}
