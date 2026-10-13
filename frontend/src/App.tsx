import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './auth'
import { AuthShell } from './components/AuthShell'
import { Layout } from './components/Layout'
import { ErrorState, Loading } from './components/ui'
import { Dashboard } from './pages/Dashboard'
import { Documents } from './pages/Documents'
import { KnowledgeBase } from './pages/KnowledgeBase'
import { Login } from './pages/Login'
import { Playground } from './pages/Playground'
import { SignUp } from './pages/SignUp'

/**
 * These guards only decide what to show. The API enforces the same rules on every request, so
 * changing the URL or the page state by hand does not give access to anything.
 */
export default function App() {
  const { state, retry } = useAuth()
  const location = useLocation()

  if (state.phase === 'checking') {
    return (
      <AuthShell>
        <Loading label="Connecting to ComplyAgent…" />
      </AuthShell>
    )
  }
  if (state.phase === 'unreachable') {
    return (
      <AuthShell>
        <ErrorState error={state.error} onRetry={retry} />
      </AuthShell>
    )
  }

  if (state.phase === 'signed-out') {
    return (
      <Routes>
        <Route path="/login" element={<Login expired={state.expired} />} />
        <Route path="/signup" element={<SignUp />} />
        {/* Remember where the person was going, to return there after signing in. */}
        <Route path="*" element={<Navigate to="/login" replace state={{ from: location.pathname }} />} />
      </Routes>
    )
  }

  const isAdmin = state.user.role === 'admin'
  const home = isAdmin ? '/' : '/playground'
  const from = (location.state as { from?: string } | null)?.from
  const afterSignIn = from && from !== '/login' && from !== '/signup' ? from : home

  return (
    <Routes>
      <Route path="/login" element={<Navigate to={afterSignIn} replace />} />
      <Route path="/signup" element={<Navigate to={home} replace />} />
      <Route element={<Layout />}>
        {isAdmin ? (
          <>
            <Route index element={<Dashboard />} />
            <Route path="documents" element={<Documents />} />
            <Route path="knowledge-base" element={<KnowledgeBase />} />
          </>
        ) : null}
        <Route path="playground" element={<Playground showModelStatus={isAdmin} />} />
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>
    </Routes>
  )
}
