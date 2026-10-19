import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { CustomerCreate } from '../api/types'
import { PaymentStrip } from '../components/visuals'
import { Notice, PageHeader } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { errorMessage, formatPercent } from '../lib/format'
import { repaymentStatus } from '../lib/risk'

/** The dataset's six months, most recent first, as the backend labels them. */
const MONTHS = ['Sep 2005', 'Aug 2005', 'Jul 2005', 'Jun 2005', 'May 2005', 'Apr 2005']
const STATUSES = [-2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
const EDUCATION = [
  { code: '1', label: 'Graduate school' },
  { code: '2', label: 'University' },
  { code: '3', label: 'High school' },
  { code: '4', label: 'Other or unknown' },
]

interface MonthForm {
  status: string
  bill: string
  paid: string
}

interface FormState {
  customerId: string
  limit: string
  age: string
  sex: '' | 'Male' | 'Female'
  education: string
  marriage: '' | 'Married' | 'Single' | 'Other'
  outcome: '' | 'paid' | 'defaulted'
  months: MonthForm[]
}

type Errors = Partial<Record<string, string>>

const EMPTY: FormState = {
  customerId: '',
  limit: '',
  age: '',
  sex: '',
  education: '',
  marriage: '',
  outcome: '',
  months: MONTHS.map(() => ({ status: '0', bill: '', paid: '' })),
}

/** A believable account that has started to slip, to try the form quickly. */
const SAMPLE: FormState = {
  customerId: '',
  limit: '120000',
  age: '38',
  sex: 'Female',
  education: '2',
  marriage: 'Married',
  outcome: 'paid',
  months: [
    { status: '2', bill: '96400', paid: '0' },
    { status: '1', bill: '93150', paid: '2000' },
    { status: '0', bill: '88700', paid: '3500' },
    { status: '0', bill: '85200', paid: '3500' },
    { status: '-1', bill: '41000', paid: '41000' },
    { status: '-1', bill: '38800', paid: '38800' },
  ],
}

/** Whole numbers only; thousands separators and spaces are allowed. */
function whole(raw: string): number | null {
  const cleaned = raw.replace(/[,\s]/g, '')
  return /^-?\d+$/.test(cleaned) ? Number(cleaned) : null
}

function validate(form: FormState): Errors {
  const errors: Errors = {}
  const id = form.customerId.trim() ? whole(form.customerId) : null
  if (form.customerId.trim() && (id === null || id < 1)) errors.customerId = 'Use a whole number from 1, or leave it blank.'
  const limit = whole(form.limit)
  if (limit === null || limit <= 0) errors.limit = 'Enter the credit limit as a whole number above 0.'
  const age = whole(form.age)
  if (age === null || age < 18 || age > 120) errors.age = 'Enter an age from 18 to 120.'
  if (!form.sex) errors.sex = 'Choose one.'
  if (!form.education) errors.education = 'Choose one.'
  if (!form.marriage) errors.marriage = 'Choose one.'
  if (!form.outcome) errors.outcome = 'Choose what happened the following month.'
  form.months.forEach((m, i) => {
    if (whole(m.bill) === null) errors[`bill-${i}`] = 'Enter the bill (0 if none).'
    const paid = whole(m.paid)
    if (paid === null || paid < 0) errors[`paid-${i}`] = 'Enter the amount paid (0 if none).'
  })
  return errors
}

function toRequest(form: FormState): CustomerCreate {
  const id = whole(form.customerId)
  return {
    ...(id !== null ? { customer_id: id } : {}),
    limit_bal: whole(form.limit)!,
    sex: form.sex as CustomerCreate['sex'],
    education_code: Number(form.education),
    marriage: form.marriage as CustomerCreate['marriage'],
    age: whole(form.age)!,
    default_next_month: form.outcome === 'defaulted',
    history: form.months.map((m) => ({
      repayment_status: Number(m.status),
      bill_amt: whole(m.bill)!,
      pay_amt: whole(m.paid)!,
    })),
  }
}

/** The model's three engineered inputs, with the backend's formulas, from what is typed so far. */
function features(form: FormState) {
  const statuses = form.months.map((m) => Number(m.status))
  const counted = statuses.filter((s) => s !== -2 && s !== 0)
  const limit = whole(form.limit)
  const bills = form.months.map((m) => whole(m.bill))
  return {
    onTime: counted.length ? statuses.filter((s) => s === -1).length / counted.length : 1,
    delay: statuses.reduce((sum, s) => sum + Math.max(s, 0), 0) / statuses.length,
    utilization:
      limit && limit > 0 && bills.every((b) => b !== null)
        ? bills.reduce<number>((sum, b) => sum + b! / limit, 0) / bills.length
        : null,
  }
}

function Field({
  label,
  error,
  hint,
  children,
}: {
  label: string
  error?: string
  hint?: ReactNode
  children: (props: { id: string; 'aria-invalid'?: true; 'aria-describedby'?: string }) => ReactNode
}) {
  const id = useId()
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null].filter(Boolean).join(' ')
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children({ id, ...(error ? { 'aria-invalid': true } : {}), ...(describedBy ? { 'aria-describedby': describedBy } : {}) })}
      {hint ? (
        <div id={`${id}-hint`} className="field-hint muted">
          {hint}
        </div>
      ) : null}
      {error ? (
        <div id={`${id}-error`} className="field-error">
          {error}
        </div>
      ) : null}
    </div>
  )
}

export function AddCustomer() {
  const navigate = useNavigate()
  const summary = useResource(api.portfolioSummary)
  const [form, setForm] = useState<FormState>(EMPTY)
  const [errors, setErrors] = useState<Errors>({})
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }))
  const setMonth = (index: number, patch: Partial<MonthForm>) =>
    setForm((f) => ({ ...f, months: f.months.map((m, i) => (i === index ? { ...m, ...patch } : m)) }))

  async function submit(event: FormEvent) {
    event.preventDefault()
    const found = validate(form)
    setErrors(found)
    setFailure(null)
    if (Object.keys(found).length) {
      setFailure(`Check the ${Object.keys(found).length === 1 ? 'highlighted field' : 'highlighted fields'} and try again.`)
      return
    }
    setBusy(true)
    try {
      const { customer, scored } = await api.createCustomer(toRequest(form))
      navigate(`/customers/${customer.customer_id}`, { state: { created: true, scored } })
    } catch (error) {
      if (error instanceof ApiError && error.code === 'customer_exists') {
        setErrors({ customerId: error.message })
      } else {
        setFailure(errorMessage(error))
      }
      setBusy(false)
    }
  }

  const f = features(form)
  const model = summary.data?.model

  return (
    <>
      <Link className="back-link" to="/customers">
        ← Customer Records
      </Link>
      <PageHeader
        eyebrow="Accounts register"
        title="Add customer"
        description="Enter one account by hand, with six months of payments. It is saved to the customer database and scored by the risk model straight away."
      />

      <form className="entry" onSubmit={submit} noValidate>
        <div className="entry-main">
          {failure ? <Notice tone="error">{failure}</Notice> : null}

          <section className="card entry-section" aria-labelledby="entry-account">
            <h2 id="entry-account">Account</h2>
            <div className="entry-grid">
              <Field label="Customer number" error={errors.customerId} hint="Optional. Blank uses the next free number.">
                {(props) => (
                  <input {...props} inputMode="numeric" value={form.customerId} placeholder="Next free number" onChange={(e) => set('customerId', e.target.value)} />
                )}
              </Field>
              <Field label="Credit limit (NT$)" error={errors.limit}>
                {(props) => <input {...props} inputMode="numeric" value={form.limit} placeholder="e.g. 120,000" onChange={(e) => set('limit', e.target.value)} />}
              </Field>
              <Field label="Age" error={errors.age}>
                {(props) => <input {...props} inputMode="numeric" value={form.age} placeholder="18–120" onChange={(e) => set('age', e.target.value)} />}
              </Field>
              <Field label="Sex" error={errors.sex}>
                {(props) => (
                  <select {...props} value={form.sex} onChange={(e) => set('sex', e.target.value as FormState['sex'])}>
                    <option value="">Choose…</option>
                    <option value="Female">Female</option>
                    <option value="Male">Male</option>
                  </select>
                )}
              </Field>
              <Field label="Education" error={errors.education}>
                {(props) => (
                  <select {...props} value={form.education} onChange={(e) => set('education', e.target.value)}>
                    <option value="">Choose…</option>
                    {EDUCATION.map((e) => (
                      <option key={e.code} value={e.code}>
                        {e.label}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              <Field label="Marital status" error={errors.marriage}>
                {(props) => (
                  <select {...props} value={form.marriage} onChange={(e) => set('marriage', e.target.value as FormState['marriage'])}>
                    <option value="">Choose…</option>
                    <option value="Married">Married</option>
                    <option value="Single">Single</option>
                    <option value="Other">Other</option>
                  </select>
                )}
              </Field>
            </div>
          </section>

          <section className="card entry-section" aria-labelledby="entry-months">
            <div>
              <h2 id="entry-months">Six months of payments</h2>
              <p className="muted small">
                Most recent month first, on the dataset's calendar. Bills can be negative when the account is in credit.
              </p>
            </div>
            <ol className="month-rows">
              {form.months.map((m, i) => (
                <li key={MONTHS[i]} className="month-row">
                  <span className="month-name">
                    {MONTHS[i]}
                    {i === 0 ? <span className="muted small"> · latest</span> : null}
                  </span>
                  <Field label="Payment">
                    {(props) => (
                      <select {...props} value={m.status} onChange={(e) => setMonth(i, { status: e.target.value })}>
                        {STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {repaymentStatus(s)}
                          </option>
                        ))}
                      </select>
                    )}
                  </Field>
                  <Field label="Bill (NT$)" error={errors[`bill-${i}`]}>
                    {(props) => <input {...props} inputMode="numeric" value={m.bill} placeholder="0" onChange={(e) => setMonth(i, { bill: e.target.value })} />}
                  </Field>
                  <Field label="Paid (NT$)" error={errors[`paid-${i}`]}>
                    {(props) => <input {...props} inputMode="numeric" value={m.paid} placeholder="0" onChange={(e) => setMonth(i, { paid: e.target.value })} />}
                  </Field>
                </li>
              ))}
            </ol>
          </section>

          <section className="card entry-section" aria-labelledby="entry-outcome">
            <div>
              <h2 id="entry-outcome">Outcome</h2>
              <p className="muted small">What happened the month after this history. It is compared with the model's prediction.</p>
            </div>
            <div className="segmented" role="radiogroup" aria-labelledby="entry-outcome" aria-describedby={errors.outcome ? 'entry-outcome-error' : undefined}>
              {(
                [
                  ['paid', 'Did not default'],
                  ['defaulted', 'Defaulted'],
                ] as const
              ).map(([value, label]) => (
                <label key={value} className={form.outcome === value ? 'segment segment-on' : 'segment'}>
                  <input type="radio" name="outcome" value={value} checked={form.outcome === value} onChange={() => set('outcome', value)} />
                  {label}
                </label>
              ))}
            </div>
            {errors.outcome ? (
              <div id="entry-outcome-error" className="field-error small">
                {errors.outcome}
              </div>
            ) : null}
          </section>
        </div>

        <aside className="entry-aside">
          <section className="card entry-summary" aria-labelledby="entry-summary">
            <span className="eyebrow">Live summary</span>
            <h2 id="entry-summary">What the model will see</h2>
            <PaymentStrip months={form.months.map((m, i) => ({ month: MONTHS[i], status: Number(m.status) }))} />
            <dl className="details">
              <div>
                <dt>Paid in full</dt>
                <dd>{formatPercent(f.onTime)} of the months that count</dd>
              </div>
              <div>
                <dt>Average delay</dt>
                <dd>{f.delay.toFixed(1)} months</dd>
              </div>
              <div>
                <dt>Credit used</dt>
                <dd>{f.utilization === null ? 'Needs the limit and every bill' : `${formatPercent(f.utilization)} on average`}</dd>
              </div>
            </dl>
            <p className="muted small">
              {summary.loading
                ? 'Checking the risk model…'
                : model
                  ? `Scored by ${model.model_name} as soon as it is saved.`
                  : 'No risk model is trained yet, so the customer is saved without a prediction.'}
            </p>
            <div className="entry-actions">
              <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
                {busy ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
                {busy ? 'Saving…' : 'Save customer'}
              </button>
              <button
                type="button"
                className="btn btn-block"
                disabled={busy}
                onClick={() => {
                  setForm(SAMPLE)
                  setErrors({})
                  setFailure(null)
                }}
              >
                Fill with sample values
              </button>
              <Link className="link-button small" to="/customers">
                Cancel
              </Link>
            </div>
          </section>
        </aside>
      </form>
    </>
  )
}
