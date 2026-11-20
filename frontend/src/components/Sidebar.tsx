import { NavLink } from 'react-router-dom'
import { NAV_GROUPS } from '../app/navigation'
import { FoundationStatus } from '../features/foundation/FoundationStatus'

export function Sidebar({
  mobileOpen,
  onCloseMobile,
}: {
  mobileOpen: boolean
  onCloseMobile: () => void
}) {
  return (
    <>
      <div
        className={`sidebar-mobile-backdrop${mobileOpen ? ' open' : ''}`}
        onClick={onCloseMobile}
        aria-hidden="true"
      />
      <aside className={`app-sidebar${mobileOpen ? ' open' : ''}`} aria-label="Menu điều hướng">
        <div className="sidebar-brand">
          <span className="sidebar-brand-title">HQTCSDL · IoT</span>
          <button
            type="button"
            className="btn-ghost btn-sm sidebar-mobile-toggle"
            aria-label="Đóng menu"
            onClick={onCloseMobile}
          >
            Đóng
          </button>
        </div>

        <nav className="sidebar-nav" aria-label="Điều hướng chính">
          {NAV_GROUPS.map(group => (
            <div key={group.id}>
              <div className="sidebar-group-title">{group.title}</div>
              <ul className="sidebar-list">
                {group.items.map(item => (
                  <li key={item.path}>
                    <NavLink
                      to={item.path}
                      className={({ isActive }) => `sidebar-link${isActive ? ' active' : ''}`}
                      onClick={onCloseMobile}
                    >
                      <span>{item.label}</span>
                      <span
                        className={
                          item.stage === 'G1' || item.stage === 'G3'
                            ? 'stage-pill stage-pill-g1'
                            : item.stage === 'G3/G4'
                              ? 'stage-pill stage-pill-g3'
                              : 'stage-pill'
                        }
                      >
                        {item.stage}
                      </span>
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <FoundationStatus compact />
          <div>Múi giờ hiển thị: UTC+07:00</div>
        </div>
      </aside>
    </>
  )
}
