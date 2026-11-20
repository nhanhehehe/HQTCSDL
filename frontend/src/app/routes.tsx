import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '../components/AppShell'
import { AreasPanel } from '../features/areas/AreasPanel'
import { useAuth } from '../features/auth/AuthProvider'
import { LoginPage } from '../features/auth/LoginPage'
import { DashboardPage } from '../features/dashboard/DashboardPage'
import { DevicesPanel } from '../features/devices/DevicesPanel'
import { MeasurementsPage } from '../features/measurements/MeasurementsPage'
import {
  AlertDetailPlaceholderPage,
  AlertsPlaceholderPage,
  MonitoringPlaceholderPage,
} from '../features/placeholders/FutureModulePages'
import { ReportsPage } from '../features/reports/ReportsPage'
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
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/measurements" element={<MeasurementsPage />} />
          <Route path="/reports" element={<ReportsPage />} />
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
