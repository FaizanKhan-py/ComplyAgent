import { useId, useState, type InputHTMLAttributes, type ReactNode } from 'react'

/** The frame shared by the sign-in and sign-up pages. */
export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title?: string
  subtitle?: string
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <main className="login">
      <div className="login-wrap">
        <div className="brand brand-lg login-brand">
          <span className="brand-mark" aria-hidden="true">✓</span> ComplyAgent
        </div>
        <p className="login-tagline muted">Policy answers you can trace to the source.</p>
        <div className="card login-card">
          {title ? (
            <div>
              <h1 className="login-title">{title}</h1>
              {subtitle ? <p className="muted small">{subtitle}</p> : null}
            </div>
          ) : null}
          {children}
        </div>
        {footer ? <p className="login-foot muted">{footer}</p> : null}
      </div>
    </main>
  )
}

interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  error?: string | null
  hint?: ReactNode
}

/** A labelled input whose error is announced and linked to the field. */
export function TextField({ label, error, hint, ...input }: TextFieldProps) {
  const id = useId()
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null].filter(Boolean)
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy.length ? describedBy.join(' ') : undefined}
        {...input}
      />
      {hint ? (
        <div id={`${id}-hint`} className="field-hint muted">
          {hint}
        </div>
      ) : null}
      {error ? (
        <div id={`${id}-error`} className="field-error" role="alert">
          {error}
        </div>
      ) : null}
    </div>
  )
}

/** A password input with a show/hide button. */
export function PasswordField({ label, error, hint, ...input }: TextFieldProps) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null].filter(Boolean)
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="password-row">
        <input
          id={id}
          type={visible ? 'text' : 'password'}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy.length ? describedBy.join(' ') : undefined}
          {...input}
        />
        <button
          type="button"
          className="btn btn-sm password-toggle"
          aria-label={`${visible ? 'Hide' : 'Show'} ${label.toLowerCase()}`}
          aria-pressed={visible}
          onClick={() => setVisible((v) => !v)}
        >
          {visible ? 'Hide' : 'Show'}
        </button>
      </div>
      {hint ? (
        <div id={`${id}-hint`} className="field-hint muted">
          {hint}
        </div>
      ) : null}
      {error ? (
        <div id={`${id}-error`} className="field-error" role="alert">
          {error}
        </div>
      ) : null}
    </div>
  )
}
