import { NavLink, Outlet } from 'react-router-dom'

const TABS = [
  { to: '/assistant', label: 'Chat', end: true },
  { to: '/assistant/documents', label: 'Documents', end: false },
  { to: '/assistant/knowledge-base', label: 'Knowledge Base', end: false },
]

/** The assistant and, for administrators, the policy documents it answers from. */
export function Assistant({ showAdminTabs }: { showAdminTabs: boolean }) {
  return (
    <>
      {showAdminTabs ? (
        <nav aria-label="AI Assistant">
          <ul className="tabs">
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
      <Outlet />
    </>
  )
}
