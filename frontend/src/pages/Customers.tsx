import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { CustomerQuery, CustomerSort, RiskLevel } from '../api/types'
import { ActualBadge, PaymentBadge, RiskBadge } from '../components/RiskBadge'
import { EmptyState, ErrorState, Loading, Notice, PageHeader } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { formatMoney, formatNumber } from '../lib/format'
import { RISK_LABEL } from '../lib/risk'
import { NoCustomerData } from './RiskDashboard'

const PAGE_SIZE = 25
const LEVELS = Object.keys(RISK_LABEL).reverse() as RiskLevel[]
const SORTS: Record<string, { label: string; sort: CustomerSort; order: 'asc' | 'desc' }> = {
  id: { label: 'Customer number', sort: 'id', order: 'asc' },
  risk: { label: 'Highest predicted risk', sort: 'probability', order: 'desc' },
  late: { label: 'Most months late', sort: 'latest_status', order: 'desc' },
  limit: { label: 'Largest credit limit', sort: 'limit_bal', order: 'desc' },
}

/** The list is described by the URL, so a filtered view can be reloaded, shared and returned to. */
function readQuery(params: URLSearchParams): CustomerQuery & { page: number } {
  const risk = params.get('risk') as RiskLevel | null
  const actual = params.get('actual')
  const sort = Object.values(SORTS).find(
    (s) => s.sort === params.get('sort') && s.order === (params.get('order') ?? 'asc'),
  )
  return {
    search: params.get('search') ?? '',
    risk: risk && LEVELS.includes(risk) ? risk : undefined,
    actual: actual === 'defaulted' || actual === 'paid' ? actual : undefined,
    sort: sort?.sort ?? 'id',
    order: sort?.order ?? 'asc',
    page: Math.max(1, Number.parseInt(params.get('page') ?? '1', 10) || 1),
  }
}

function Results({ query, onPage }: { query: CustomerQuery & { page: number }; onPage: (page: number) => void }) {
  const list = useResource(() => api.listCustomers({ ...query, page_size: PAGE_SIZE }))
  const data = list.data
  const filtered = Boolean(query.search || query.risk || query.actual)

  if (list.loading) return <Loading label="Loading customers…" />
  if (list.error && !data) return <ErrorState error={list.error} onRetry={list.refresh} />
  if (!data) return null
  if (data.total === 0) {
    return filtered ? (
      <EmptyState title="No customers match">Change the search or the filters to see more.</EmptyState>
    ) : (
      <NoCustomerData />
    )
  }

  const first = (data.page - 1) * data.page_size + 1
  const last = first + data.customers.length - 1
  const pages = Math.ceil(data.total / data.page_size)

  return (
    <>
      {list.error ? <Notice tone="warn">Showing the last known list. {list.error.message}</Notice> : null}
      {data.customers.length === 0 ? (
        <EmptyState title="No customers on this page" />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Customer</th>
                <th scope="col" className="num">Credit limit</th>
                <th scope="col">Latest payment</th>
                <th scope="col">Predicted risk</th>
                <th scope="col">Actual status</th>
              </tr>
            </thead>
            <tbody>
              {data.customers.map((c) => (
                <tr key={c.customer_id}>
                  <td data-label="Customer">
                    <div>
                      <Link className="filename" to={`/customers/${c.customer_id}`}>
                        Customer {c.customer_id}
                      </Link>
                      <div className="muted small">
                        Age {c.age} · {c.education}
                      </div>
                    </div>
                  </td>
                  <td data-label="Credit limit" className="num">{formatMoney(c.limit_bal)}</td>
                  <td data-label="Latest">
                    <PaymentBadge status={c.latest_status} />
                  </td>
                  <td data-label="Predicted">
                    <RiskBadge level={c.risk_level} probability={c.default_probability} />
                  </td>
                  <td data-label="Actual">
                    <ActualBadge defaulted={c.actual_default} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="pager">
        <span className="muted small">
          {data.customers.length
            ? `Showing ${formatNumber(first)}–${formatNumber(last)} of ${formatNumber(data.total)}`
            : `${formatNumber(data.total)} in total`}
        </span>
        <div className="page-actions">
          <button type="button" className="btn btn-sm" disabled={data.page <= 1} onClick={() => onPage(data.page - 1)}>
            Previous
          </button>
          <button type="button" className="btn btn-sm" disabled={data.page >= pages} onClick={() => onPage(data.page + 1)}>
            Next
          </button>
        </div>
      </div>
    </>
  )
}

export function Customers() {
  const [params, setParams] = useSearchParams()
  const query = readQuery(params)
  const [search, setSearch] = useState(query.search ?? '')
  const sortKey = Object.keys(SORTS).find((k) => SORTS[k].sort === query.sort) ?? 'id'

  /** Changing a filter goes back to the first page. */
  const update = useCallback(
    (changes: Record<string, string | undefined>, keepPage = false) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current)
          if (!keepPage) next.delete('page')
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value)
            else next.delete(key)
          }
          return next
        },
        { replace: true },
      )
    },
    [setParams],
  )

  // Search as the person types, after a short pause.
  const applied = query.search ?? ''
  useEffect(() => {
    if (search.trim() === applied) return
    const id = window.setTimeout(() => update({ search: search.trim() || undefined }), 300)
    return () => window.clearTimeout(id)
  }, [search, applied, update])

  return (
    <>
      <PageHeader
        title="Customer Records"
        description="Account details, payment history, risk predictions and actual status."
      />
      <section className="card" aria-label="Customers">
        <div className="filters">
          <label className="field filter-search">
            <span>Search</span>
            <input
              type="search"
              value={search}
              inputMode="numeric"
              maxLength={12}
              placeholder="Customer number"
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Predicted risk</span>
            <select value={query.risk ?? ''} onChange={(e) => update({ risk: e.target.value || undefined })}>
              <option value="">Any</option>
              {LEVELS.map((level) => (
                <option key={level} value={level}>
                  {RISK_LABEL[level]}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Actual status</span>
            <select value={query.actual ?? ''} onChange={(e) => update({ actual: e.target.value || undefined })}>
              <option value="">Any</option>
              <option value="defaulted">Defaulted</option>
              <option value="paid">Did not default</option>
            </select>
          </label>
          <label className="field">
            <span>Sort by</span>
            <select
              value={sortKey}
              onChange={(e) => {
                const choice = SORTS[e.target.value]
                update({
                  sort: choice.sort === 'id' ? undefined : choice.sort,
                  order: choice.order === 'asc' ? undefined : choice.order,
                })
              }}
            >
              {Object.entries(SORTS).map(([key, choice]) => (
                <option key={key} value={key}>
                  {choice.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        {/* Remounting on a new query starts a fresh request and drops any stale answer. */}
        <Results key={JSON.stringify(query)} query={query} onPage={(page) => update({ page: String(page) }, true)} />
      </section>
    </>
  )
}
