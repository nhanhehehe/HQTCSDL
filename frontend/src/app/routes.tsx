import { Navigate, Route, Routes } from 'react-router-dom'
import { AreasPanel } from '../features/areas/AreasPanel'
import { AuthPanel } from '../features/auth/AuthPanel'
import { useAuth } from '../features/auth/AuthProvider'
import { DevicesPanel } from '../features/devices/DevicesPanel'
import { FoundationStatus } from '../features/foundation/FoundationStatus'
import { DEFAULT_AUTHENTICATED_ROUTE, resolveAuthView } from './navigation'

function RouteStub({ title, detail }: { title: string; detail: string }) {
  return (
    <section>
      <h2>{title}</h2>
      <p>{detail}</p>
    </section>
  )
}

function AuthenticatedRoutes() {
  const auth = useAuth()
  return (
    <main>
      <p className="eyebrow">HQTCSDL · QUẢN TRỊ HỆ THỐNG</p>
      <h1>Hệ thống giám sát nông trại</h1>
      <FoundationStatus />
      <AuthPanel />
      {!auth.verified && auth.session && (
        <section role="status">
          <p>Đang giữ phiên hiện tại nhưng chưa xác minh lại với Backend; thao tác ghi đang tạm khóa.</p>
        </section>
      )}
      <Routes>
        <Route path="/" element={<Navigate to={DEFAULT_AUTHENTICATED_ROUTE} replace />} />
        <Route path="/login" element={<Navigate to={DEFAULT_AUTHENTICATED_ROUTE} replace />} />
        <Route path="/areas" element={<AreasPanel />} />
        <Route path="/devices" element={<DevicesPanel />} />
        <Route path="/dashboard" element={<RouteStub title="Tổng quan (G3/G4)" detail="Chưa triển khai — khung chờ giai đoạn G3/G4." />} />
        <Route path="/measurements" element={<RouteStub title="Dữ liệu đo (G3)" detail="Chưa triển khai — khung chờ giai đoạn G3 (FL-07)." />} />
        <Route path="/reports" element={<RouteStub title="Báo cáo (G3)" detail="Chưa triển khai — khung chờ giai đoạn G3 (FL-13)." />} />
        <Route path="/monitoring" element={<RouteStub title="Giám sát & Ngưỡng (G4)" detail="Chưa triển khai — khung chờ giai đoạn G4 (FL-04/FL-08)." />} />
        <Route path="/alerts" element={<RouteStub title="Cảnh báo (G4)" detail="Chưa triển khai — khung chờ giai đoạn G4 (FL-11)." />} />
        <Route path="/alerts/:id" element={<RouteStub title="Chi tiết cảnh báo (G4)" detail="Chưa triển khai — khung chờ giai đoạn G4 (FL-11)." />} />
        <Route path="*" element={<Navigate to={DEFAULT_AUTHENTICATED_ROUTE} replace />} />
      </Routes>
    </main>
  )
}

export function AppRoutes() {
  const auth = useAuth()
  const view = resolveAuthView(auth)

  if (view === 'initializing') {
    return (
      <main>
        <section aria-busy="true" role="status">
          <h2>Đang kiểm tra phiên quản trị…</h2>
          <p>Hệ thống đang xác minh phiên hiện tại với Backend trước khi điều hướng.</p>
        </section>
      </main>
    )
  }

  if (view === 'uncertain_logout') {
    return (
      <main>
        <FoundationStatus />
        <AuthPanel />
      </main>
    )
  }

  if (view === 'unauthenticated') {
    return (
      <Routes>
        <Route
          path="/login"
          element={
            <main>
              <p className="eyebrow">HQTCSDL · ĐĂNG NHẬP</p>
              <h1>Hệ thống giám sát nông trại</h1>
              <FoundationStatus />
              <AuthPanel />
            </main>
          }
        />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  if (!auth.session) return null
  return <AuthenticatedRoutes key={auth.session.csrf_token} />
}
