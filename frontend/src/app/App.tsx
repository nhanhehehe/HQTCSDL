import { FoundationStatus } from '../features/foundation/FoundationStatus'
import { AuthProvider } from '../features/auth/AuthProvider'
import { AuthPanel } from '../features/auth/AuthPanel'
import { AreasPanel } from '../features/areas/AreasPanel'
import { DevicesPanel } from '../features/devices/DevicesPanel'

export function App() {
  return <AuthProvider><main>
    <p className="eyebrow">HQTCSDL · NỀN TẢNG PHÁT TRIỂN</p>
    <h1>Hệ thống giám sát nông trại</h1>
    <p>Khung ứng dụng dùng chung cho nhóm Backend, Frontend và IoT.</p>
    <FoundationStatus />
    <AuthPanel />
    <AreasPanel />
    <DevicesPanel />
    <aside>Giám sát và cảnh báo sẽ được triển khai theo thiết kế luồng đã review.</aside>
  </main></AuthProvider>
}
