import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '../components/AppShell'
import { AreasPanel } from '../features/areas/AreasPanel'
import { useAuth } from '../features/auth/AuthProvider'
import { LoginPage } from '../features/auth/LoginPage'
import { DevicesPanel } from '../features/devices/DevicesPanel'
import {
  AlertDetailPlaceholderPage,
  AlertsPlaceholderPage,
  DashboardPlaceholderPage,
  MeasurementsPlaceholderPage,
  MonitoringPlaceholderPage,
  ReportsPlaceholderPage,
} from '../features/placeholders/FutureModulePages'
import { DEFAULT_AUTHENTICATED_ROUTE, resolveAuthView } from './navigation'
import { OperationalFarmProvider, OperationalFarmSelector } from './OperationalFarmContext'

function AuthenticatedRoutes() {
  return (
    <OperationalFarmProvider>
      <AppShell farmSelector={<OperationalFarmSelector />}>
        <Routes>
          <Route path="/" element={<Navigate to={DEFAULT_AUTHENTICATED_ROUTE} replace />} />
          <Route path="/login" element={<Navigate to={DEFAULT_AUTHENTICATED_ROUTE} replace />} />
          <Route path="/areas" element={<AreasPanel />} />
          <Route path="/devices" element={<DevicesPanel />} />
          <Route path="/dashboard" element={<DashboardPlaceholderPage />} />
          <Route path="/measurements" element={<MeasurementsPlaceholderPage />} />
          <Route path="/reports" element={<ReportsPlaceholderPage />} />
          <Route path="/monitoring" element={<MonitoringPlaceholderPage />} />
          <Route path="/alerts" element={<AlertsPlaceholderPage />} />
          <Route path="/alerts/:id" element={<AlertDetailPlaceholderPage />} />
          <Route path="*" element={<Navigate to={DEFAULT_AUTHENTICATED_ROUTE} replace />} />
        </Routes>
      </AppShell>
    </OperationalFarmProvider>
  )
}

export function AppRoutes() {
  const auth = useAuth()
  const view = resolveAuthView(auth)

  if (view === 'initializing') {
    return (
      <div className="login-shell">
        <div className="login-container">
          <section className="card" aria-busy="true" role="status">
            <h2>Đang kiểm tra phiên quản trị…</h2>
            <p className="muted">
              Hệ thống đang xác minh phiên hiện tại với Backend trước khi điều hướng.
            </p>
          </section>
        </div>
      </div>
    )
  }

  if (view === 'uncertain_logout') {
    return <LoginPage />
  }

  if (view === 'unauthenticated') {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  if (!auth.session) return null
  return <AuthenticatedRoutes key={auth.session.csrf_token} />
}
