import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth'
import { Layout } from './components/Layout'
import { ErrorState, Loading } from './components/ui'
import { Dashboard } from './pages/Dashboard'
import { Documents } from './pages/Documents'
import { KnowledgeBase } from './pages/KnowledgeBase'
import { Login } from './pages/Login'
import { Playground } from './pages/Playground'

export default function App() {
  const { state, retry } = useAuth()

  if (state.phase === 'checking') {
    return (
      <main className="login">
        <Loading label="Connecting to ComplyAgent…" />
      </main>
    )
  }
  if (state.phase === 'unreachable') {
    return (
      <main className="login">
        <div className="card login-card">
          <ErrorState error={state.error} onRetry={retry} />
        </div>
      </main>
    )
  }
  if (state.phase === 'signed-out') return <Login expired={state.expired} />

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="documents" element={<Documents />} />
        <Route path="knowledge-base" element={<KnowledgeBase />} />
        <Route path="playground" element={<Playground />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
