import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { getRouteTitle } from '../app/navigation'
import { useAuth } from '../features/auth/AuthProvider'
import { StatusBadge } from './StatusBadge'

function formatSessionExpiry(iso: string): string {
  return new Date(iso).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })
}

export function Topbar({
  mobileNavOpen,
  onToggleMobileNav,
  farmSelector,
}: {
  mobileNavOpen: boolean
  onToggleMobileNav: () => void
  farmSelector?: ReactNode
}) {
  const auth = useAuth()
  const location = useLocation()
  const session = auth.session

  return (
    <header className="app-topbar">
      <div className="topbar-left">
        <button
          type="button"
          className="btn-secondary btn-sm sidebar-mobile-toggle"
          aria-label="Mở menu điều hướng"
          aria-expanded={mobileNavOpen}
          onClick={onToggleMobileNav}
        >
          Menu
        </button>
        <span className="topbar-title">{getRouteTitle(location.pathname)}</span>
      </div>

      {farmSelector}

      {session && (
        <div className="topbar-right">
          <div className="session-meta">
            <strong>{session.admin.username}</strong>
            <small className="tabular" title="Hết hạn phiên (UTC+07:00)">
              Hết hạn: {formatSessionExpiry(session.expires_at)}
            </small>
            <StatusBadge
              tone={auth.verified ? 'ok' : 'warn'}
              label={auth.verified ? 'Đã xác minh' : 'Cần kiểm tra phiên'}
            />
          </div>
          <button
            type="button"
            className="btn-secondary btn-sm"
            disabled={auth.busy}
            onClick={() => void auth.verify()}
          >
            Kiểm tra phiên
          </button>
          <button
            type="button"
            className="btn-sm"
            disabled={auth.busy}
            onClick={() => void auth.logout()}
          >
            Đăng xuất
          </button>
        </div>
      )}
    </header>
  )
}
