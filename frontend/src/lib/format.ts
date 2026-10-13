export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value)
}

/** Whole New Taiwan dollars, the currency of the customer data. */
export function formatMoney(value: number | null): string {
  if (value === null) return '—'
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'TWD',
    maximumFractionDigits: 0,
  }).format(value)
}

/** Large totals in short form, e.g. NT$1.5B. */
export function formatMoneyCompact(value: number): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'TWD',
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

export function formatPercent(ratio: number, digits = 0): string {
  return new Intl.NumberFormat(undefined, {
    style: 'percent',
    maximumFractionDigits: digits,
  }).format(ratio)
}

export function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const date = new Date(`${iso.slice(0, 10)}T00:00:00`)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong.'
}
