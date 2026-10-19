import { useId, useRef, useState, type InputHTMLAttributes, type PointerEvent, type ReactNode } from 'react'

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
    <div className="login">
      <HeroPanel />
      <main className="login-stage">
        <div className="login-wrap">
          <div className="brand brand-lg login-brand">
            <BrandMark /> ComplyAgent
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
    </div>
  )
}

/**
 * The ComplyAgent mark: a faceted shield carrying a "C" whose check-mark breaks out through its
 * opening. Fixed brand colours, so it reads the same on the dark sidebar and on paper.
 */
export function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32">
        <path fill="#e2581f" d="M16 1.8 27.4 5.9V15c0 7.3-4.9 13.1-11.4 15.4C9.5 28.1 4.6 22.3 4.6 15V5.9Z" />
        <path fill="#b8410f" d="M16 1.8 27.4 5.9V15c0 7.3-4.9 13.1-11.4 15.4Z" />
        <path
          fill="none"
          stroke="#fff"
          strokeOpacity=".28"
          strokeWidth=".8"
          d="M16 3.3 26 6.9V15c0 6.4-4.2 11.6-10 13.8C10.2 26.6 6 21.4 6 15V6.9Z"
        />
        <g fill="none" stroke="#fbf3e6" strokeLinecap="round" strokeLinejoin="round">
          <path strokeWidth="2.5" d="M19 10.3a6 6 0 1 0 1.9 8.6" />
          <path strokeWidth="2.5" d="m12.7 15.4 2.7 2.7 7.4-7.9" />
        </g>
      </svg>
    </span>
  )
}

/**
 * The showcase beside the sign-in form on wide screens. The illustration shows what the product
 * does: a policy clause is highlighted, cited, and weighed against a customer's risk.
 */
function HeroPanel() {
  const ref = useRef<HTMLElement>(null)
  function onPointerMove(event: PointerEvent<HTMLElement>) {
    const box = ref.current?.getBoundingClientRect()
    if (!box) return
    ref.current!.style.setProperty('--px', ((event.clientX - box.left) / box.width - 0.5).toFixed(3))
    ref.current!.style.setProperty('--py', ((event.clientY - box.top) / box.height - 0.5).toFixed(3))
  }
  return (
    <aside className="login-hero" aria-label="About ComplyAgent" ref={ref} onPointerMove={onPointerMove}>
      <div className="brand hero-brand">
        <BrandMark /> ComplyAgent
      </div>
      <div className="hero-copy">
        <span className="eyebrow">Credit risk · Policy · Evidence</span>
        <p className="hero-title">
          Every decision, <em>on the record.</em>
        </p>
        <p className="hero-sub">
          Score the whole portfolio for default risk, then ask the policies what to do about it. Every
          answer points to the clause it came from.
        </p>
      </div>

      <figure className="exhibit" aria-hidden="true">
        <svg viewBox="0 0 560 300">
          {/* The policy page */}
          <g className="layer layer-back">
            <path className="sheet" d="M24 22h196l30 30v226H24Z" />
            <path className="sheet-fold" d="M220 22v30h30" />
            <text className="ex-mono" x="44" y="56">CREDIT POLICY</text>
            <text className="ex-serif" x="44" y="80">§4 Delinquency</text>
            {[104, 122, 140].map((y, n) => (
              <rect key={y} className="line" x="44" y={y} width={[176, 160, 170][n]} height="6" rx="3" />
            ))}
            <rect className="marker" x="40" y="155" width="184" height="16" rx="2" />
            <rect className="line line-strong" x="44" y="160" width="150" height="6" rx="3" />
            {[184, 202, 220, 238].map((y, n) => (
              <rect key={y} className="line" x="44" y={y} width={[168, 120, 172, 96][n]} height="6" rx="3" />
            ))}
          </g>
          {/* The citation it becomes */}
          <path className="thread" d="M226 163 C 280 163, 280 74, 330 74" />
          <circle className="thread-dot" cx="226" cy="163" r="4" />
          <g className="layer layer-mid">
            <rect className="card-ex" x="330" y="42" width="206" height="66" rx="6" />
            <text className="ex-cite" x="346" y="68">[1]</text>
            <text className="ex-mono" x="374" y="68">§4.2 · 60+ days</text>
            <rect className="meter" x="346" y="84" width="174" height="5" rx="2.5" />
            <rect className="meter-fill" x="346" y="84" width="152" height="5" rx="2.5" />
          </g>
          {/* The customer it applies to */}
          <path className="thread thread-2" d="M433 108 C 433 140, 433 150, 433 168" />
          <g className="layer layer-front">
            <rect className="card-ex" x="330" y="168" width="206" height="110" rx="6" />
            <path className="dial-track" d="M372 252a61 61 0 0 1 122 0" pathLength={100} />
            <path className="dial-value" d="M372 252a61 61 0 0 1 122 0" pathLength={100} />
            <text className="ex-number" x="433" y="248" textAnchor="middle">82%</text>
            <g className="ex-stamp">
              <rect x="452" y="182" width="72" height="24" rx="3" />
              <text x="488" y="198" textAnchor="middle">ESCALATE</text>
            </g>
          </g>
        </svg>
        <figcaption>
          <span>A clause, cited</span>
          <span>A customer, scored</span>
        </figcaption>
      </figure>

      <ol className="hero-points">
        <li>Default predictions for every account</li>
        <li>Answers grounded in your own policies</li>
        <li>Sources ranked, quoted and one click away</li>
      </ol>
    </aside>
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
