import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth'

const NAV = [
  { to: '/', label: 'Risk Dashboard', icon: 'chart', end: true, adminOnly: true },
  { to: '/customers', label: 'Customer Records', icon: 'people', end: false, adminOnly: true },
  { to: '/assistant', label: 'AI Assistant', icon: 'chat', end: false, adminOnly: false },
] as const

const ICON_PATHS = {
  chart: 'M4 20V10m5.5 10V4M15 20v-7m5.5 7V8',
  people:
    'M16 20v-1.5a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4V20m6.5-9.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm11.5 9.5v-1.5a4 4 0 0 0-3-3.87m-1.5-11a3.5 3.5 0 0 1 0 6.75',
  chat: 'M20.5 11.5a8 8 0 0 1-11.7 7.1L3.5 20l1.4-5.3a8 8 0 1 1 15.6-3.2Z',
}

function NavIcon({ name }: { name: keyof typeof ICON_PATHS }) {
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path d={ICON_PATHS[name]} />
    </svg>
  )
}

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
                  <NavIcon name={item.icon} />
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
