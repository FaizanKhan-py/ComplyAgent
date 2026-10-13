import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/documents', label: 'Documents', end: false },
  { to: '/knowledge-base', label: 'Knowledge Base', end: false },
  { to: '/playground', label: 'RAG Playground', end: false },
]

export function Layout() {
  const { state, signOut } = useAuth()
  const authRequired = state.phase === 'signed-in' && state.authRequired

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">✓</span> ComplyAgent
          <span className="brand-tag">Admin</span>
        </div>
        <nav aria-label="Main">
          <ul className="nav">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to} end={item.end} className="nav-link">
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="sidebar-foot">
          {authRequired ? (
            <button type="button" className="btn btn-ghost btn-sm" onClick={signOut}>
              Sign out
            </button>
          ) : (
            <span className="muted small" title="ADMIN_API_TOKEN is not set on the server">
              Open access (dev mode)
            </span>
          )}
        </div>
      </aside>
      <main id="main" className="main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  )
}
