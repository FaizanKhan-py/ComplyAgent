import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './auth'
import { AuthShell } from './components/AuthShell'
import { Layout } from './components/Layout'
import { ErrorState, Loading } from './components/ui'
import { AddCustomer } from './pages/AddCustomer'
import { Assistant } from './pages/Assistant'
import { AuditTrail } from './pages/AuditTrail'
import { CollectionsDashboard } from './pages/CollectionsDashboard'
import { CustomerRecord } from './pages/CustomerRecord'
import { Customers } from './pages/Customers'
import { Documents } from './pages/Documents'
import { FollowUps } from './pages/FollowUps'
import { Inbox } from './pages/Inbox'
import { Login } from './pages/Login'
import { Playground } from './pages/Playground'
import { PredictDataset } from './pages/PredictDataset'
import { RiskDashboard } from './pages/RiskDashboard'
import { SignUp } from './pages/SignUp'

/**
 * Shown while a reload asks the server who is signed in. Not the sign-in frame: that made the
 * login page flash before the app appeared. The spinner waits a moment, so a quick check shows
 * nothing at all.
 */
function SessionCheck() {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), 300)
    return () => clearTimeout(timer)
  }, [])
  return <div className="session-check">{slow ? <Loading label="Connecting to ComplyAgent…" /> : null}</div>
}

/**
 * These guards only decide what to show. The API enforces the same rules on every request, so
 * changing the URL or the page state by hand does not give access to anything.
 */
export default function App() {
  const { state, retry } = useAuth()
  const location = useLocation()

  if (state.phase === 'checking') return <SessionCheck />

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
  const home = isAdmin ? '/' : '/assistant'
  const from = (location.state as { from?: string } | null)?.from
  const afterSignIn = from && from !== '/login' && from !== '/signup' ? from : home

  return (
    <Routes>
      <Route path="/login" element={<Navigate to={afterSignIn} replace />} />
      <Route path="/signup" element={<Navigate to={home} replace />} />
      <Route element={<Layout />}>
        {isAdmin ? (
          <>
            <Route index element={<RiskDashboard />} />
            <Route path="customers" element={<Customers />} />
            <Route path="customers/new" element={<AddCustomer />} />
            <Route path="customers/:customerId" element={<CustomerRecord />} />
            <Route path="predict" element={<PredictDataset />} />
            <Route path="collections" element={<CollectionsDashboard />} />
            <Route path="approvals" element={<Inbox />} />
            <Route path="follow-ups" element={<FollowUps />} />
            <Route path="audit" element={<AuditTrail />} />
          </>
        ) : null}
        <Route path="assistant" element={<Assistant showAdminTabs={isAdmin} />}>
          <Route index element={<Playground showModelStatus={isAdmin} />} />
          {isAdmin ? <Route path="documents" element={<Documents />} /> : null}
        </Route>
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>
    </Routes>
  )
}
