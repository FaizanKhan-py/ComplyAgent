import type { RiskLevel } from '../api/types'

export const RISK_LABEL: Record<RiskLevel, string> = {
  low: 'Low risk',
  medium: 'Medium risk',
  high: 'High risk',
}

/** The probability bands behind each level. */
export const RISK_RANGE: Record<RiskLevel, string> = {
  low: 'under 40%',
  medium: '40% to 69%',
  high: '70% or more',
}

/** The dataset's repayment status code for one month, in words. */
export function repaymentStatus(status: number | null): string {
  if (status === null) return '—'
  if (status <= -2) return 'No spending'
  if (status === -1) return 'Paid in full'
  if (status === 0) return 'Paid the minimum'
  return `${status} month${status === 1 ? '' : 's'} late`
}
