import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useAuth } from '../auth'
import { AuthShell, PasswordField, TextField } from '../components/AuthShell'
import { Notice } from '../components/ui'
import {
  emailError,
  nameError,
  PASSWORD_MIN_LENGTH,
  passwordError,
  passwordStrength,
} from '../lib/validation'

type Field = 'fullName' | 'email' | 'password' | 'confirm'
type Errors = Partial<Record<Field, string | null>>

/** Server error codes that belong next to one field instead of at the top of the form. */
const FIELD_FOR_CODE: Record<string, Field> = {
  invalid_name: 'fullName',
  invalid_email: 'email',
  email_taken: 'email',
  weak_password: 'password',
}

export function SignUp() {
  const { signUp } = useAuth()
  const navigate = useNavigate()
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [errors, setErrors] = useState<Errors>({})
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const strength = passwordStrength(password)

  function validate(): Errors {
    return {
      fullName: nameError(fullName),
      email: emailError(email),
      password: passwordError(password, email),
      confirm: !confirm
        ? 'Enter the password again.'
        : confirm !== password
          ? 'The passwords do not match.'
          : null,
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const found = validate()
    setErrors(found)
    setFailure(null)
    if (Object.values(found).some(Boolean)) return

    setBusy(true)
    try {
      const user = await signUp({ full_name: fullName.trim(), email: email.trim(), password })
      void navigate('/login', { replace: true, state: { registered: user.email } })
    } catch (error) {
      const field = error instanceof ApiError ? FIELD_FOR_CODE[error.code] : undefined
      const message = error instanceof Error ? error.message : 'Sign-up failed.'
      if (field) setErrors({ [field]: message })
      else setFailure(message)
      setBusy(false)
    }
  }

  return (
    <AuthShell
      title="Create your account"
      subtitle="Ask policy questions and see the exact passages behind every answer."
      footer={
        <>
          Already have an account? <Link to="/login">Sign in</Link>
        </>
      }
    >
      {failure ? <Notice tone="error">{failure}</Notice> : null}

      <form className="login-form" onSubmit={submit} noValidate>
        <TextField
          label="Full name"
          name="name"
          autoComplete="name"
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
          error={errors.fullName}
          autoFocus
        />
        <TextField
          label="Email address"
          type="email"
          name="email"
          autoComplete="username"
          inputMode="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          error={errors.email}
        />
        <PasswordField
          label="Password"
          name="new-password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={errors.password}
          hint={
            password ? (
              <span className="strength" data-score={strength.score}>
                <span className="strength-bar" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                  <span />
                </span>
                <span>Password strength: {strength.label}</span>
              </span>
            ) : (
              `At least ${PASSWORD_MIN_LENGTH} characters. A longer phrase is stronger.`
            )
          }
        />
        <PasswordField
          label="Confirm password"
          name="confirm-password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          error={errors.confirm}
        />
        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
          {busy ? 'Creating account…' : 'Create account'}
        </button>
      </form>
      <p className="muted small">
        New accounts can use the RAG Playground. Managing documents needs an administrator account.
      </p>
    </AuthShell>
  )
}
