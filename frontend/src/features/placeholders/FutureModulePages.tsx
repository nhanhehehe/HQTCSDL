import { useParams } from 'react-router-dom'
import { useOptionalOperationalFarm } from '../../app/OperationalFarmContext'
import { FeaturePlaceholder } from '../../components/FeaturePlaceholder'
import { PageHeader } from '../../components/PageHeader'
import { StatusBadge } from '../../components/StatusBadge'

function OperationalFarmContextNotice() {
  const farmCtx = useOptionalOperationalFarm()
  if (!farmCtx) return null

  if (farmCtx.error) {
    return (
      <div className="banner banner-err" role="alert">
        <span>Lỗi tải ngữ cảnh Farm từ Backend: {farmCtx.error}</span>
      </div>
    )
  }

  if (!farmCtx.selectedFarm) {
    return (
      <div className="banner banner-warn" role="status">
        <span>
          Chưa chọn Farm vận hành. Vui lòng chọn tường minh một Farm từ bộ chọn trên Topbar (hệ
          thống không tự chọn Farm đầu tiên).
        </span>
      </div>
    )
  }

  return (
    <div className="card stack-sm" role="region" aria-label="Ngữ cảnh Farm đang chọn">
      <div className="row-between">
        <div className="row">
          <strong>Ngữ cảnh Farm đang chọn:</strong>
          <span className="mono">{farmCtx.selectedFarm.code}</span>
          <span>{farmCtx.selectedFarm.name}</span>
          <StatusBadge
            tone={farmCtx.selectedFarm.archived_at ? 'neutral' : 'ok'}
            label={farmCtx.selectedFarm.archived_at ? 'Đã lưu trữ' : 'Đang hoạt động'}
          />
        </div>
        <small className="tabular mono">contextGeneration={farmCtx.contextGeneration}</small>
      </div>
      <small>
        Khi triển khai G3/G4, thay đổi Farm trên Topbar sẽ tăng contextGeneration để reset
        cursor/view và hủy mọi phản hồi API cũ.
      </small>
    </div>
  )
}

export function MonitoringPlaceholderPage() {
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
        contextSlot={<OperationalFarmContextNotice />}
      />
    </div>
  )
}

export function AlertsPlaceholderPage() {
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
        contextSlot={<OperationalFarmContextNotice />}
      />
    </div>
  )
}

export function AlertDetailPlaceholderPage() {
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
        contextSlot={<OperationalFarmContextNotice />}
      />
    </div>
  )
}
