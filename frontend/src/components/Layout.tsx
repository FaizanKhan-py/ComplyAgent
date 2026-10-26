import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'
import { useActiveRect } from '../lib/motion'
import { BrandMark } from './AuthShell'
import { ChatWidget } from './ChatWidget'

const NAV = [
  { to: '/', label: 'Risk Dashboard', icon: 'chart', end: true, adminOnly: true },
  { to: '/customers', label: 'Customer Records', icon: 'people', end: false, adminOnly: true },
  { to: '/predict', label: 'Predict Dataset', icon: 'upload', end: false, adminOnly: true },
  { to: '/assistant', label: 'AI Assistant', icon: 'chat', end: false, adminOnly: false },
] as const

const ICON_PATHS = {
  chart: 'M4 20V10m5.5 10V4M15 20v-7m5.5 7V8',
  people:
    'M16 20v-1.5a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4V20m6.5-9.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm11.5 9.5v-1.5a4 4 0 0 0-3-3.87m-1.5-11a3.5 3.5 0 0 1 0 6.75',
  upload: 'M12 15V4m0 0-4 4m4-4 4 4M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3',
  chat: 'M20.5 11.5a8 8 0 0 1-11.7 7.1L3.5 20l1.4-5.3a8 8 0 1 1 15.6-3.2Z',
  menu: 'M4 6h16M4 12h16M4 18h16',
  close: 'M6 6l12 12M18 6 6 18',
}

function NavIcon({ name }: { name: keyof typeof ICON_PATHS }) {
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path d={ICON_PATHS[name]} />
    </svg>
  )
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  const letters = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : (parts[0] ?? '?').slice(0, 2)
  return letters.toUpperCase()
}

export function Layout() {
  const { user, signOut } = useAuth()
  const { pathname } = useLocation()
  const [leaving, setLeaving] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const toggleRef = useRef<HTMLButtonElement>(null)
  const [navRef, active] = useActiveRect<HTMLUListElement>(pathname)
  const isAdmin = user?.role === 'admin'
  // The assistant animates its own tabs, so its sub-pages share one page key.
  const pageKey = pathname.startsWith('/assistant') ? '/assistant' : pathname

  // On small screens the menu is a drop-down panel; Escape closes it and hands focus back.
  useEffect(() => {
    if (!menuOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false)
        toggleRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [menuOpen])

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className={menuOpen ? 'sidebar sidebar-open' : 'sidebar'}>
        <div className="sidebar-top">
          <div className="brand">
            <BrandMark /> ComplyAgent
            {isAdmin ? <span className="brand-tag">Admin</span> : null}
          </div>
          <button
            ref={toggleRef}
            type="button"
            className="menu-toggle"
            aria-expanded={menuOpen}
            aria-controls="sidebar-panel"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <NavIcon name={menuOpen ? 'close' : 'menu'} />
            <span className="visually-hidden">{menuOpen ? 'Close menu' : 'Open menu'}</span>
          </button>
        </div>
        <div id="sidebar-panel" className="sidebar-panel">
          <nav aria-label="Main">
            <ul className="nav" ref={navRef}>
              <li
                className="nav-indicator"
                aria-hidden="true"
                style={
                  active
                    ? { opacity: 1, transform: `translateY(${active.top}px)`, height: active.height }
                    : { opacity: 0 }
                }
              />
              {NAV.filter((item) => isAdmin || !item.adminOnly).map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className="nav-link"
                    onClick={() => setMenuOpen(false)}
                  >
                    <NavIcon name={item.icon} />
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          {user ? (
            <div className="sidebar-foot">
              <div className="account-row">
                <span className="avatar" aria-hidden="true">
                  {initials(user.full_name)}
                </span>
                <div className="account" aria-label="Signed-in account">
                  <strong className="account-name">{user.full_name}</strong>
                  <span className="muted small account-email">{user.email}</span>
                  <span className="muted small">{isAdmin ? 'Administrator' : 'Member'}</span>
                </div>
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
        </div>
      </aside>
      <main id="main" className="main" tabIndex={-1}>
        <div className="page" key={pageKey}>
          <Outlet />
        </div>
      </main>
      {/* The full-page chat is the same assistant, so the pop-up hides there. */}
      <ChatWidget isAdmin={isAdmin} hidden={pathname === '/assistant'} />
    </div>
  )
}
