import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useActiveRect } from '../lib/motion'

const TABS = [
  { to: '/assistant', label: 'Chat', end: true },
  { to: '/assistant/documents', label: 'Documents', end: false },
]

/** The assistant and, for administrators, the policy documents it answers from. */
export function Assistant({ showAdminTabs }: { showAdminTabs: boolean }) {
  const { pathname } = useLocation()
  const [tabsRef, active] = useActiveRect<HTMLUListElement>(pathname)
  return (
    <>
      {showAdminTabs ? (
        <nav aria-label="AI Assistant">
          <ul className="tabs" ref={tabsRef}>
            <li
              className="tab-indicator"
              aria-hidden="true"
              style={
                active
                  ? { opacity: 1, transform: `translateX(${active.left}px)`, width: active.width }
                  : { opacity: 0 }
              }
            />
            {TABS.map((tab) => (
              <li key={tab.to}>
                <NavLink to={tab.to} end={tab.end} className="tab">
                  {tab.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      ) : null}
      <div className="page-sub" key={pathname}>
        <Outlet />
      </div>
    </>
  )
}
