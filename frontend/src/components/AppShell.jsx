import { NavLink, Outlet } from 'react-router-dom'

export default function AppShell() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <NavLink to="/" end title="Fleet">
          🏠
        </NavLink>
        <NavLink to="/alerts" title="Fuel Alerts — AI agent">
          🚨
        </NavLink>
        <NavLink to="/live" title="Live Map">
          📊
        </NavLink>
        <NavLink to="/admin" title="Admin — add vehicle">
          ⚙️
        </NavLink>
      </aside>
      <div className="main">
        <Outlet />
      </div>
    </div>
  )
}
