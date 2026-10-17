import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { CustomerGuidance } from '../api/types'
import { errorMessage } from '../lib/format'
import { AnswerContent } from './AnswerContent'
import { Notice } from './ui'

/**
 * Asks the policy assistant what applies to this customer. Each answer comes from the indexed
 * policies with citations, so it changes when the policies change.
 */
export function PolicyGuidance({ customerId }: { customerId: number }) {
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')
  const [guidance, setGuidance] = useState<CustomerGuidance | null>(null)
  const [error, setError] = useState('')
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])

  async function load() {
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    setState('loading')
    try {
      setGuidance(await api.customerGuidance(customerId, controller.signal))
      setState('done')
    } catch (err) {
      if (controller.signal.aborted) return
      setError(errorMessage(err))
      setState('error')
    }
  }

  return (
    <section className="card" aria-label="Policy guidance">
      <div className="card-head">
        <div>
          <h2>Policy guidance</h2>
          <p className="muted small">What the indexed policies say about this customer's situation.</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => void load()} disabled={state === 'loading'}>
          {state === 'loading' ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
          {state === 'done' ? 'Check again' : 'Check the policies'}
        </button>
      </div>

      {state === 'loading' ? (
        <p className="muted" role="status">
          Asking the policy assistant four questions. With a local model this can take up to a minute.
        </p>
      ) : null}
      {state === 'error' ? <Notice tone="error">{error}</Notice> : null}
      {state === 'done' && guidance ? (
        guidance.sections.length === 0 ? (
          <p>This account is not overdue, so no collections policy applies to it right now.</p>
        ) : (
          <>
            <p className="muted small">
              {guidance.months_late} month{guidance.months_late === 1 ? '' : 's'} late, treated as about{' '}
              {guidance.days_overdue_estimate} days overdue: the data records lateness in whole months. Answers are
              guidance for staff to check, not decisions.
            </p>
            <ol className="guidance">
              {guidance.sections.map((section) => (
                <li key={section.key} className="guidance-item">
                  <h3>{section.title}</h3>
                  <p className="muted small">Asked: {section.question}</p>
                  <AnswerContent response={section} idPrefix={`guidance-${section.key}`} />
                </li>
              ))}
            </ol>
          </>
        )
      ) : null}
    </section>
  )
}
