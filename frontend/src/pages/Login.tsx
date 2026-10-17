import { useState, type FormEvent } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useAuth } from '../auth'
import { AuthShell, PasswordField, TextField } from '../components/AuthShell'
import { Notice } from '../components/ui'
import { emailError } from '../lib/validation'

interface LoginLocationState {
  from?: string
  /** Set by the sign-up page so the new account's email is already filled in. */
  registered?: string
}

export function Login({ expired = false }: { expired?: boolean }) {
  const { signIn } = useAuth()
  const location = useLocation()
  const routed = (location.state as LoginLocationState | null) ?? {}

  const [email, setEmail] = useState(routed.registered ?? '')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<{ email?: string | null; password?: string | null }>({})
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [showRecovery, setShowRecovery] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    const found = {
      email: emailError(email),
      password: password ? null : 'Enter your password.',
    }
    setErrors(found)
    setFailure(null)
    if (found.email || found.password) return

    setBusy(true)
    try {
      await signIn(email.trim(), password)
      // On success the app re-renders into the signed-in routes; nothing more to do here.
    } catch (error) {
      setPassword('')
      setFailure(
        error instanceof ApiError || error instanceof Error ? error.message : 'Sign-in failed.',
      )
      setBusy(false)
    }
  }

  return (
    <AuthShell
      title="Sign in"
      subtitle="Use your ComplyAgent account to continue."
      footer={
        <>
          New to ComplyAgent? <Link to="/signup">Create an account</Link>
        </>
      }
    >
      {routed.registered && !failure ? (
        <Notice tone="ok">Your account was created. Sign in to continue.</Notice>
      ) : null}
      {expired && !failure && !routed.registered ? (
        <Notice tone="warn">Your session ended. Sign in again.</Notice>
      ) : null}
      {failure ? <Notice tone="error">{failure}</Notice> : null}

      <form className="login-form" onSubmit={submit} noValidate>
        <TextField
          label="Email address"
          type="email"
          name="email"
          autoComplete="username"
          inputMode="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          error={errors.email}
          autoFocus={!routed.registered}
        />
        <PasswordField
          label="Password"
          name="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={errors.password}
          autoFocus={Boolean(routed.registered)}
        />
        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      <div>
        <button
          type="button"
          className="link-button small"
          aria-expanded={showRecovery}
          onClick={() => setShowRecovery((open) => !open)}
        >
          Forgot your password?
        </button>
        {showRecovery ? (
          <p className="muted small recovery">
            Passwords cannot be reset by email on this server. Ask a ComplyAgent administrator to
            reset it for you; they will give you a new password to sign in with.
          </p>
        ) : null}
      </div>
    </AuthShell>
  )
}
