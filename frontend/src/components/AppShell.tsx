import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuth } from '../features/auth/AuthProvider'
import { Sidebar } from './Sidebar'
import { StateBanner } from './StateBanner'
import { Topbar } from './Topbar'

export function AppShell({
  farmSelector,
  children,
}: {
  farmSelector?: ReactNode
  children: ReactNode
}) {
  const auth = useAuth()
  const location = useLocation()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  useEffect(() => {
    setMobileNavOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!mobileNavOpen) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setMobileNavOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [mobileNavOpen])

  return (
    <div className="app-shell">
      <Sidebar mobileOpen={mobileNavOpen} onCloseMobile={() => setMobileNavOpen(false)} />
      <div className="app-main-wrap">
        <Topbar
          mobileNavOpen={mobileNavOpen}
          onToggleMobileNav={() => setMobileNavOpen(prev => !prev)}
          farmSelector={farmSelector}
        />
        <main className="app-main">
          {auth.session && !auth.verified && (
            <StateBanner
              tone="warn"
              message={
                auth.message ||
                'Chưa xác minh lại phiên với Backend; đang giữ giao diện và biểu mẫu hiện tại, các thao tác ghi tạm khóa.'
              }
              action={
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  disabled={auth.busy}
                  onClick={() => void auth.verify()}
                >
                  Kiểm tra phiên lại
                </button>
              }
            />
          )}
          {children}
        </main>
      </div>
    </div>
  )
}
