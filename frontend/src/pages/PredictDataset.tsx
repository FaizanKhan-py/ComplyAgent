import { useMemo, useRef, useState, type DragEvent } from 'react'
import { api } from '../api/client'
import type { DatasetPrediction, PredictionModelInfo, RiskLevel } from '../api/types'
import { RiskBadge } from '../components/RiskBadge'
import { Badge, EmptyState, ErrorState, Loading, Notice, PageHeader, StatCard } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { errorMessage, formatMoney, formatNumber, formatPercent } from '../lib/format'
import { RISK_LABEL } from '../lib/risk'

const PAGE_SIZE = 25
const EXTENSIONS = ['.csv', '.xls', '.xlsx']
const LEVELS: RiskLevel[] = ['high', 'medium', 'low']

function ModelStatus({ info }: { info: PredictionModelInfo }) {
  if (!info.available) {
    return <Notice tone="warn">{info.message}</Notice>
  }
  const test = info.test_metrics
  return (
    <dl className="details">
      <div>
        <dt>Model</dt>
        <dd>{info.model_name}, saved by notebooks/Data_pipeline_and_Modeling.ipynb</dd>
      </div>
      <div>
        <dt>Features</dt>
        <dd className="mono small">{info.features.join(', ')}</dd>
      </div>
      {test ? (
        <div>
          <dt>Notebook test scores</dt>
          <dd>
            ROC-AUC {test.roc_auc?.toFixed(3)} · recall {formatPercent(test.recall ?? 0)} · accuracy{' '}
            {formatPercent(test.accuracy ?? 0)}
          </dd>
        </div>
      ) : null}
    </dl>
  )
}

function download(result: DatasetPrediction) {
  const url = URL.createObjectURL(new Blob([result.csv], { type: 'text/csv' }))
  const link = document.createElement('a')
  link.href = url
  link.download = result.filename.replace(/\.(csv|xlsx?)$/i, '') + '_predicted.csv'
  link.click()
  URL.revokeObjectURL(url)
}

function Results({ result }: { result: DatasetPrediction }) {
  const [risk, setRisk] = useState<RiskLevel | ''>('')
  const [page, setPage] = useState(1)
  const rows = useMemo(
    () => (risk ? result.predictions.filter((p) => p.risk_level === risk) : result.predictions),
    [result, risk],
  )
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const shown = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
  const scores = result.scores

  return (
    <>
      <section className="grid grid-4" aria-label="Prediction summary">
        <StatCard label="Rows scored" value={formatNumber(result.rows)} hint={result.filename} />
        <StatCard
          label="Predicted to default"
          value={formatNumber(result.predicted_defaults)}
          hint={`${formatPercent(result.predicted_defaults / result.rows, 1)} of rows`}
          tone={result.predicted_defaults ? 'warn' : 'neutral'}
        />
        <StatCard
          label="High risk"
          value={formatNumber(result.by_risk.high)}
          hint={`Medium ${formatNumber(result.by_risk.medium)} · low ${formatNumber(result.by_risk.low)}`}
          tone={result.by_risk.high ? 'error' : 'neutral'}
        />
        {scores ? (
          <StatCard
            label="Accuracy vs. actual"
            value={formatPercent(scores.accuracy, 1)}
            hint={`Recall ${formatPercent(scores.recall)} · precision ${formatPercent(scores.precision)}`}
          />
        ) : (
          <StatCard label="Accuracy vs. actual" value="—" hint="The file had no actual target values" />
        )}
      </section>

      <section className="card" aria-labelledby="rows-title">
        <div className="card-head">
          <h2 id="rows-title">Predictions</h2>
          <button type="button" className="btn btn-primary" onClick={() => download(result)}>
            Download CSV with target column
          </button>
        </div>
        <div className="filters">
          <label className="field">
            <span>Predicted risk</span>
            <select
              value={risk}
              onChange={(event) => {
                setRisk(event.target.value as RiskLevel | '')
                setPage(1)
              }}
            >
              <option value="">Any</option>
              {LEVELS.map((level) => (
                <option key={level} value={level}>
                  {RISK_LABEL[level]}
                </option>
              ))}
            </select>
          </label>
        </div>
        {shown.length === 0 ? (
          <EmptyState title="No rows match this filter" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Row</th>
                  <th scope="col" className="num">Credit limit</th>
                  <th scope="col" className="num">Age</th>
                  <th scope="col">Default payment next month</th>
                  <th scope="col">Risk</th>
                  {scores ? <th scope="col">Actual</th> : null}
                </tr>
              </thead>
              <tbody>
                {shown.map((p) => (
                  <tr key={p.row}>
                    <td data-label="Row">
                      <div>
                        <span className="filename">Row {p.row}</span>
                        {p.id !== null ? <div className="muted small">ID {p.id}</div> : null}
                      </div>
                    </td>
                    <td data-label="Credit limit" className="num">{formatMoney(p.limit_bal)}</td>
                    <td data-label="Age" className="num">{p.age}</td>
                    <td data-label="Prediction">
                      <Badge tone={p.predicted_default ? 'error' : 'ok'}>
                        {p.predicted_default ? '1 · Will default' : '0 · Will not default'}
                      </Badge>
                    </td>
                    <td data-label="Risk">
                      <RiskBadge level={p.risk_level} probability={p.default_probability} />
                    </td>
                    {scores ? (
                      <td data-label="Actual">{p.actual_default === null ? '—' : p.actual_default}</td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pager">
          <span className="muted small">
            {rows.length
              ? `Showing ${formatNumber((page - 1) * PAGE_SIZE + 1)}–${formatNumber(
                  Math.min(page * PAGE_SIZE, rows.length),
                )} of ${formatNumber(rows.length)}`
              : '0 rows'}
          </span>
          <div className="page-actions">
            <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <button type="button" className="btn btn-sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>
              Next
            </button>
          </div>
        </div>
      </section>
    </>
  )
}

export function PredictDataset() {
  const model = useResource(api.predictionModel)
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [running, setRunning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<DatasetPrediction | null>(null)

  async function run(file: File) {
    setError(null)
    if (!EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))) {
      setError(`“${file.name}” is not a CSV or Excel file. Upload a ${EXTENSIONS.join(', ')} file.`)
      return
    }
    setRunning(file.name)
    try {
      setResult(await api.predictDataset(file))
    } catch (err) {
      setError(`Could not score “${file.name}”: ${errorMessage(err)}`)
    } finally {
      setRunning(null)
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    const file = event.dataTransfer.files[0]
    if (file) void run(file)
  }

  const ready = model.data?.available ?? false

  return (
    <>
      <PageHeader
        title="Predict Dataset"
        description="Upload a customer dataset and the notebook's model predicts “default payment next month” for every row."
      />

      <section className="card" aria-labelledby="model-title">
        <h2 id="model-title">Model</h2>
        {model.loading ? (
          <Loading label="Checking the model…" />
        ) : model.error ? (
          <ErrorState error={model.error} onRetry={model.refresh} />
        ) : model.data ? (
          <ModelStatus info={model.data} />
        ) : null}
      </section>

      <section className="card" aria-labelledby="upload-title">
        <h2 id="upload-title">Upload dataset</h2>
        <div
          className={`dropzone ${dragging ? 'dropzone-active' : ''}`}
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          {running ? (
            <Loading label={`Scoring every row of ${running}…`} />
          ) : (
            <>
              <p>
                <strong>Drop a CSV or Excel file here</strong> or
              </p>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!ready}
                onClick={() => inputRef.current?.click()}
              >
                Choose file
              </button>
            </>
          )}
          <input
            ref={inputRef}
            type="file"
            className="visually-hidden"
            aria-label="Choose a dataset to score"
            accept={EXTENSIONS.join(',')}
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (file) void run(file)
            }}
          />
          <p className="muted small">
            CSV, .xls or .xlsx (first sheet) with the UCI credit card columns, named X1–X23 or LIMIT_BAL, SEX, EDUCATION, MARRIAGE, AGE,
            PAY_0, PAY_2–PAY_6, BILL_AMT1–6, PAY_AMT1–6. An ID column is optional. If the file already
            has “default payment next month” (or Y), it is kept as the actual value and compared.
          </p>
        </div>
      </section>

      {error ? (
        <Notice tone="error" onDismiss={() => setError(null)}>
          {error}
        </Notice>
      ) : null}

      {result ? <Results key={result.filename + result.rows} result={result} /> : null}
    </>
  )
}
