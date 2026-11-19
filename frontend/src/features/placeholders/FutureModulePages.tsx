import type { ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { FeaturePlaceholder } from '../../components/FeaturePlaceholder'
import { PageHeader } from '../../components/PageHeader'

export function DashboardPlaceholderPage({ contextSlot }: { contextSlot?: ReactNode }) {
  return (
    <div className="stack">
      <PageHeader
        eyebrow="Vận hành · G3/G4"
        title="Tổng quan Farm"
        description="Hiển thị số đo hiện tại toàn Farm, độ mới dữ liệu và thông báo cảnh báo đang mở theo một snapshot đồng bộ."
      />
      <FeaturePlaceholder
        stage="G3/G4"
        flows="FL-12"
        scopeSummary="G3 triển khai phần số đo hiện tại và độ mới hiển thị (FreshnessView); G4 bổ sung thông báo cảnh báo đang mở (active_alerts), active_count và trạng thái đối soát (EvaluationState)."
        plannedApis={['GET /api/v1/dashboard']}
        contractNotes={[
          'Cổng chặn trước khi code Dashboard G3: cần đối chiếu và chốt hợp đồng tham số farm_id bắt buộc của GET /api/v1/dashboard giữa FL-12 §4 cũ và phần bổ sung ngày 18/11/2026.',
          'Không hiển thị số 0 giả cho cảnh báo ở G3 trước khi có G4; không hiển thị last_new_persisted_at hoặc backend_received_at trên Dashboard.',
        ]}
        contextSlot={contextSlot}
      />
    </div>
  )
}

export function MeasurementsPlaceholderPage({ contextSlot }: { contextSlot?: ReactNode }) {
  return (
    <div className="stack">
      <PageHeader
        eyebrow="Vận hành · G3"
        title="Dữ liệu đo"
        description="Tra cứu số đo hiện tại, lịch sử có giờ đo (Known), bản ghi chưa xác định giờ đo (Unknown) và biểu đồ chuỗi thời gian."
      />
      <FeaturePlaceholder
        stage="G3"
        flows="FL-07"
        scopeSummary="Đọc Measurement & MeasurementValue theo Node thuộc Farm/Zone đã chọn; tách riêng bản hiện tại khỏi bộ lọc lịch sử và giữ chung view_token cho bảng lịch sử và biểu đồ Known."
        plannedApis={[
          'GET /api/v1/nodes/{id}/measurements/current',
          'GET /api/v1/nodes/{id}/measurements?time_mode=known|unknown',
          'GET /api/v1/nodes/{id}/measurements/chart',
        ]}
        contractNotes={[
          'Thư viện biểu đồ sẽ được chọn và cài ở bước G3 (UI-D07), bảo đảm không tự downsample và ngắt nét tại giá trị null.',
        ]}
        contextSlot={contextSlot}
      />
    </div>
  )
}

export function ReportsPlaceholderPage({ contextSlot }: { contextSlot?: ReactNode }) {
  return (
    <div className="stack">
      <PageHeader
        eyebrow="Vận hành · G3"
        title="Báo cáo thống kê & CSV"
        description="Tổng hợp n_valid, n_invalid, min, max, avg cho 5 đại lượng chuẩn theo khoảng thời gian và xuất CSV đúng snapshot đã ký."
      />
      <FeaturePlaceholder
        stage="G3"
        flows="FL-13"
        scopeSummary="Tạo snapshot thống kê 5 đại lượng (làm tròn half-up 2 chữ số, tách bản UNKNOWN) và xuất file CSV UTF-8 BOM cùng kết quả qua snapshot_token (TTL 15 phút)."
        plannedApis={[
          'POST /api/v1/reports/measurements',
          'POST /api/v1/reports/measurements/csv',
        ]}
        contractNotes={[
          'Tách biệt Draft State và Result Snapshot; sửa bộ lọc sẽ khóa nút Xuất CSV cho tới khi Tạo báo cáo mới thành công.',
        ]}
        contextSlot={contextSlot}
      />
    </div>
  )
}

export function MonitoringPlaceholderPage({ contextSlot }: { contextSlot?: ReactNode }) {
  return (
    <div className="stack">
      <PageHeader
        eyebrow="Cấu hình & Quản trị · G4"
        title="Giám sát, Ngưỡng & Quyền tiếp nhận"
        description="Cấu hình ngưỡng 5 đại lượng, bật/tắt giám sát từng đại lượng, thời gian timeout T của Node và quyền Backend tiếp nhận bản ghi mới."
      />
      <FeaturePlaceholder
        stage="G4"
        flows="FL-04, FL-08"
        scopeSummary="Bật/tắt quyền tiếp nhận Node/Gateway tại Backend và quản lý 2 chuỗi phiên bản cấu hình độc lập (THRESHOLD và TIMEOUT) kèm kiểm tra profile thời gian."
        plannedApis={[
          'PATCH /api/v1/nodes/{id}/receiving',
          'PATCH /api/v1/gateways/{id}/receiving',
          'GET /api/v1/nodes/{id}/monitoring',
          'PATCH /api/v1/nodes/{id}/monitoring',
        ]}
        contextSlot={contextSlot}
      />
    </div>
  )
}

export function AlertsPlaceholderPage({ contextSlot }: { contextSlot?: ReactNode }) {
  return (
    <div className="stack">
      <PageHeader
        eyebrow="Vận hành · G4"
        title="Quản lý cảnh báo"
        description="Theo dõi các đợt cảnh báo đang mở (Active), tra cứu lịch sử (History) và đối chiếu trạng thái đánh giá theo từng Node."
      />
      <FeaturePlaceholder
        stage="G4"
        flows="FL-09, FL-10, FL-11"
        scopeSummary="Hiển thị danh sách cảnh báo THRESHOLD và TIMEOUT phân trang theo cursor, lọc theo vòng đời/ghi nhận/phục hồi tự nhiên và truy cập màn hình chi tiết audit."
        plannedApis={['GET /api/v1/alerts']}
        contextSlot={contextSlot}
      />
    </div>
  )
}

export function AlertDetailPlaceholderPage({ contextSlot }: { contextSlot?: ReactNode }) {
  const { id } = useParams()
  return (
    <div className="stack">
      <PageHeader
        eyebrow="Vận hành · G4"
        title={`Chi tiết cảnh báo #${id ?? '—'}`}
        description="Xem bằng chứng bất biến của episode/revision, ghi nhận xử lý và tra cứu lịch sử audit (revisions, acknowledgements, replacements)."
      />
      <FeaturePlaceholder
        stage="G4"
        flows="FL-11"
        scopeSummary="Hiển thị snapshot bằng chứng, kiểm tra version chống ghi đè khi replay (409 ALERT_VERSION_CONFLICT) và ghi nhận xử lý idempotent."
        plannedApis={[
          'GET /api/v1/alerts/{id}?audit_kind=revisions|acknowledgements|replacements',
          'POST /api/v1/alerts/{id}/acknowledgements',
        ]}
        contextSlot={contextSlot}
      />
    </div>
  )
}
