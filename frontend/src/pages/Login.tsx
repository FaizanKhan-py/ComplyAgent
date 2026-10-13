import { useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import { useAuth } from '../auth'
import { Notice } from '../components/ui'

export function Login({ expired }: { expired: boolean }) {
  const { signIn } = useAuth()
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!token.trim()) return
    setBusy(true)
    setError(null)
    try {
      await signIn(token.trim())
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? 'That token was not accepted.'
          : err instanceof Error
            ? err.message
            : 'Sign-in failed.',
      )
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand brand-lg">
          <span className="brand-mark" aria-hidden="true">✓</span> ComplyAgent Admin
        </div>
        <p className="muted">Enter the admin token configured on the server to continue.</p>
        {expired && !error ? <Notice tone="warn">Your session ended. Sign in again.</Notice> : null}
        {error ? <Notice tone="error">{error}</Notice> : null}
        <label className="field">
          <span>Admin token</span>
          <input
            type="password"
            autoComplete="current-password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            required
            autoFocus
          />
        </label>
        <button type="submit" className="btn btn-primary" disabled={busy || !token.trim()}>
          {busy ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
          Sign in
        </button>
      </form>
    </main>
  )
}
