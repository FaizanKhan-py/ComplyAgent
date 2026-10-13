import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth'

const NAV = [
  { to: '/', label: 'Dashboard', end: true, adminOnly: true },
  { to: '/documents', label: 'Documents', end: false, adminOnly: true },
  { to: '/knowledge-base', label: 'Knowledge Base', end: false, adminOnly: true },
  { to: '/playground', label: 'RAG Playground', end: false, adminOnly: false },
]

export function Layout() {
  const { user, signOut } = useAuth()
  const [leaving, setLeaving] = useState(false)
  const isAdmin = user?.role === 'admin'

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">✓</span> ComplyAgent
          {isAdmin ? <span className="brand-tag">Admin</span> : null}
        </div>
        <nav aria-label="Main">
          <ul className="nav">
            {NAV.filter((item) => isAdmin || !item.adminOnly).map((item) => (
              <li key={item.to}>
                <NavLink to={item.to} end={item.end} className="nav-link">
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        {user ? (
          <div className="sidebar-foot">
            <div className="account" aria-label="Signed-in account">
              <strong className="account-name">{user.full_name}</strong>
              <span className="muted small account-email">{user.email}</span>
              <span className="muted small">{isAdmin ? 'Administrator' : 'Member'}</span>
            </div>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={leaving}
              onClick={() => {
                setLeaving(true)
                void signOut()
              }}
            >
              {leaving ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        ) : null}
      </aside>
      <main id="main" className="main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  )
}
