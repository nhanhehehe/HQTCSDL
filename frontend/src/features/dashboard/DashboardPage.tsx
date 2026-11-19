import { useEffect, useState, useSyncExternalStore } from 'react'
import { Link } from 'react-router-dom'
import { useOptionalOperationalFarm } from '../../app/OperationalFarmContext'
import { EmptyState } from '../../components/EmptyState'
import { MetricCard } from '../../components/MetricCard'
import { PageHeader } from '../../components/PageHeader'
import { StateBanner } from '../../components/StateBanner'
import { StatusBadge } from '../../components/StatusBadge'
import { useAuth } from '../auth/AuthProvider'
import {
  METRIC_META,
  areaLifecycleBadge,
  formatUtcToVi,
  freshnessBadge,
  receivingBadge,
} from '../measurements/format'
import { DashboardCoordinator } from './coordinator'

export function DashboardPage() {
  const { session } = useAuth()
  return session ? <DashboardViewPanel key={session.csrf_token} /> : null
}

function DashboardViewPanel() {
  const farmCtx = useOptionalOperationalFarm()
  const selectedFarm = farmCtx?.selectedFarm ?? null

  const [coordinator] = useState(() => new DashboardCoordinator())
  const snap = useSyncExternalStore(coordinator.subscribe, coordinator.getSnapshot)

  useEffect(() => {
    const onVisibility = () => coordinator.setVisibility(document.hidden)
    const onResume = () => coordinator.handleResume()
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('focus', onResume)
    window.addEventListener('online', onResume)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('focus', onResume)
      window.removeEventListener('online', onResume)
      coordinator.dispose()
    }
  }, [coordinator])

  useEffect(() => {
    coordinator.setFarm(selectedFarm?.id ?? '')
  }, [coordinator, selectedFarm?.id, farmCtx?.contextGeneration])

  const view = snap.snapshot
  const nodes = view?.nodes ?? []
  const focusedNode =
    nodes.find(n => n.profile.id === snap.selectedNodeId) ?? nodes[0] ?? null

  const freshCount = nodes.filter(n => n.freshness.state === 'FRESH').length
  const staleCount = nodes.filter(n => n.freshness.state === 'STALE').length
  const aheadCount = nodes.filter(n => n.freshness.state === 'CLOCK_AHEAD').length

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Vận hành · G3/G4 · FL-12"
        title="Tổng quan Farm"
        description="Theo dõi số đo hiện tại toàn Farm và độ mới mẫu hiển thị theo một snapshot đồng bộ."
        actions={
          selectedFarm && (
            <button
              type="button"
              className="btn-secondary btn-sm"
              disabled={snap.loading || snap.refreshing}
              onClick={() => coordinator.refreshNow()}
            >
              {snap.loading || snap.refreshing ? 'Đang cập nhật…' : 'Làm mới ngay'}
            </button>
          )
        }
      />

      {!selectedFarm && (
        <StateBanner
          tone="warn"
          message="Chưa chọn Farm vận hành. Vui lòng chọn tường minh một Farm từ bộ chọn trên Topbar (hệ thống không tự chọn Farm đầu tiên)."
        />
      )}

      {selectedFarm && snap.error && snap.showingStale && snap.lastSuccessAt && (
        <StateBanner
          tone="warn"
          message={`Chưa cập nhật — đang hiển thị lần thành công lúc ${formatUtcToVi(snap.lastSuccessAt)}. Lỗi gần nhất: ${snap.error}`}
          action={
            <button
              type="button"
              className="btn-secondary btn-sm"
              onClick={() => coordinator.refreshNow()}
            >
              Thử lại
            </button>
          }
        />
      )}

      {selectedFarm && snap.error && !snap.showingStale && (
        <StateBanner
          tone="err"
          message={`Không tải được dữ liệu Dashboard: ${snap.error}`}
          action={
            <button
              type="button"
              className="btn-secondary btn-sm"
              onClick={() => coordinator.refreshNow()}
            >
              Thử lại
            </button>
          }
        />
      )}

      {selectedFarm && snap.loading && !view && (
        <p className="muted" role="status">
          Đang tải snapshot tổng quan Farm…
        </p>
      )}

      {view && (
        <>
          <section className="card stack-sm" aria-label="Thông tin snapshot Farm">
            <div className="row-between">
              <div className="row">
                <strong>{view.scope.farm.code}</strong>
                <span>{view.scope.farm.name}</span>
                <StatusBadge
                  tone={areaLifecycleBadge(view.scope.farm.archived_at).tone}
                  label={areaLifecycleBadge(view.scope.farm.archived_at).label}
                />
              </div>
              <small className="tabular muted">
                Chốt lúc: <strong>{formatUtcToVi(view.as_of)}</strong> · Tự động cập nhật mỗi 5 giây
              </small>
            </div>
          </section>

          <section className="kpi-grid" aria-label="Chỉ số tóm tắt Farm">
            <div className="card kpi-card">
              <small className="muted">Số Node thuộc Farm</small>
              <div className="kpi-value tabular">{nodes.length}</div>
              <small className="muted">Scope: {view.scope.kind}</small>
            </div>

            <div className="card kpi-card">
              <small className="muted">Độ mới dữ liệu (Freshness)</small>
              <div className="kpi-value tabular">
                {freshCount} FRESH / {staleCount} STALE
              </div>
              <small className="muted">
                {aheadCount > 0 ? `${aheadCount} CLOCK_AHEAD · ` : ''}
                Độc lập với giám sát &amp; cảnh báo
              </small>
            </div>

            <div className="card kpi-card">
              <small className="muted">Cảnh báo đang mở</small>
              <div className="kpi-value muted">Chờ G4</div>
              <small className="muted">
                capabilities.alerts = false (chưa kích hoạt ở G3)
              </small>
            </div>

            <div className="card kpi-card">
              <small className="muted">Đối soát &amp; Giám sát</small>
              <div className="kpi-value muted">Chờ G4</div>
              <small className="muted">
                capabilities.monitoring = false (chưa kích hoạt ở G3)
              </small>
            </div>
          </section>

          <div className="dashboard-split">
            <section className="card stack" aria-label="Số đo hiện tại theo Node">
              <div className="card-header">
                <h2>Thẻ số đo theo Node</h2>
                {nodes.length > 0 && (
                  <small className="muted">
                    Chọn Node chỉ đổi thẻ hiển thị bên trái, không làm thay đổi ngữ cảnh toàn Farm.
                  </small>
                )}
              </div>

              {nodes.length === 0 ? (
                <EmptyState
                  title="Farm chưa có Node nào"
                  description="Bạn có thể đăng ký Node ở mục Thiết bị & Khóa (/devices)."
                  action={
                    <Link className="btn btn-secondary btn-sm" to="/devices">
                      Mở Thiết bị &amp; Khóa
                    </Link>
                  }
                />
              ) : (
                <>
                  <div className="tabs" role="tablist" aria-label="Danh sách Node trong Farm">
                    {nodes.map(item => {
                      const active = focusedNode?.profile.id === item.profile.id
                      return (
                        <button
                          key={item.profile.id}
                          type="button"
                          role="tab"
                          aria-selected={active}
                          className={`tab-btn${active ? ' active' : ''}`}
                          onClick={() => coordinator.selectNode(item.profile.id)}
                        >
                          {item.profile.device_id} · {item.profile.name}
                        </button>
                      )
                    })}
                  </div>

                  {focusedNode && (
                    <div className="stack" role="region" aria-label="Chi tiết số đo Node đang chọn">
                      <div className="row-between">
                        <div className="row">
                          <strong>{focusedNode.profile.device_id}</strong>
                          <span>{focusedNode.profile.name}</span>
                          <span className="muted">
                            · Zone {focusedNode.profile.zone.code}
                            {focusedNode.profile.zone.archived_at ? ' (Đã lưu trữ)' : ''}
                          </span>
                          <span className="muted">
                            ·{' '}
                            {focusedNode.profile.gateway
                              ? `GW ${focusedNode.profile.gateway.gateway_id}`
                              : 'Chưa gán Gateway'}
                          </span>
                        </div>
                      </div>

                      <div className="row">
                        <StatusBadge
                          tone={receivingBadge(focusedNode.profile.receiving_enabled).tone}
                          label={receivingBadge(focusedNode.profile.receiving_enabled).label}
                        />
                        <StatusBadge
                          tone={freshnessBadge(focusedNode.freshness.state).tone}
                          label={`Độ mới: ${freshnessBadge(focusedNode.freshness.state).label}`}
                        />
                        <StatusBadge tone="neutral" label="Giám sát & Đối soát: Chờ G4" />
                      </div>

                      <div className="row-between">
                        <small className="tabular">
                          <strong>Giờ đo:</strong>{' '}
                          {focusedNode.current.measurement
                            ? formatUtcToVi(focusedNode.current.measurement.event_time)
                            : 'Chưa có'}
                        </small>
                        <small className="tabular muted">
                          {focusedNode.freshness.age_seconds !== null
                            ? `Tuổi mẫu: ${focusedNode.freshness.age_seconds}s`
                            : 'Tuổi mẫu: —'}
                          {focusedNode.freshness.stale_after_seconds !== null
                            ? ` (ngưỡng hiển thị: ${focusedNode.freshness.stale_after_seconds}s)`
                            : ' (thiếu profile thời gian)'}
                        </small>
                      </div>

                      {focusedNode.current.state === 'NO_DATA' && (
                        <EmptyState
                          title="Chưa có dữ liệu"
                          description="Node chưa có bản ghi nào được lưu trên Backend."
                        />
                      )}

                      {focusedNode.current.state === 'NO_EVENT_TIME' && (
                        <EmptyState
                          title="Chưa có dữ liệu xác định giờ đo"
                          description="Backend hiện chỉ có bản ghi UNKNOWN (thiếu giờ đo) và không gán giờ nhận làm giờ đo."
                          action={
                            <Link
                              className="btn btn-secondary btn-sm"
                              to={`/measurements?node_id=${encodeURIComponent(focusedNode.profile.id)}&mode=unknown`}
                            >
                              Mở nhóm UNKNOWN tại Dữ liệu đo
                            </Link>
                          }
                        />
                      )}

                      {focusedNode.current.state === 'AVAILABLE' &&
                        focusedNode.current.measurement && (
                          <div className="metric-grid">
                            {METRIC_META.map(meta => (
                              <MetricCard
                                key={meta.code}
                                label={meta.label}
                                metric={
                                  focusedNode.current.measurement?.measurements[meta.code] ?? null
                                }
                                fallbackUnit={meta.unit}
                              />
                            ))}
                          </div>
                        )}

                      <div className="row">
                        <Link
                          className="btn btn-secondary btn-sm"
                          to={`/measurements?node_id=${encodeURIComponent(focusedNode.profile.id)}`}
                        >
                          Xem lịch sử (FL-07)
                        </Link>
                        <Link
                          className="btn btn-secondary btn-sm"
                          to={`/reports?node_id=${encodeURIComponent(focusedNode.profile.id)}`}
                        >
                          Tạo báo cáo (FL-13)
                        </Link>
                      </div>
                    </div>
                  )}
                </>
              )}
            </section>

            <aside className="card stack" aria-label="Khối thông báo cảnh báo G4">
              <div className="card-header">
                <h2>Thông báo &amp; Cảnh báo (G4)</h2>
                <StatusBadge tone="neutral" label="Chờ G4" />
              </div>
              <EmptyState
                title="Cảnh báo &amp; thông báo sẽ kích hoạt ở G4"
                description="Tại giai đoạn G3 (FL-12 phần dữ liệu), trường active_count, monitoring, evaluation và active_alerts trả null nhằm giữ ranh giới trung thực và không hiển thị số 0 giả."
                action={
                  <Link className="btn btn-secondary btn-sm" to="/alerts">
                    Xem lộ trình Cảnh báo G4
                  </Link>
                }
              />
            </aside>
          </div>
        </>
      )}
    </div>
  )
}
