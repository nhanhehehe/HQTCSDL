import { FoundationStatus } from '../features/foundation/FoundationStatus'

export function App() {
  return <main>
    <p className="eyebrow">HQTCSDL · NỀN TẢNG PHÁT TRIỂN</p>
    <h1>Hệ thống giám sát nông trại</h1>
    <p>Khung ứng dụng dùng chung cho nhóm Backend, Frontend và IoT.</p>
    <FoundationStatus />
    <aside>Chức năng nghiệp vụ và giám sát cảnh báo sẽ được triển khai theo thiết kế luồng đã review.</aside>
  </main>
}
