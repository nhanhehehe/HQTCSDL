import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Link } from 'react-router-dom'
import { useOptionalOperationalFarm } from '../../app/OperationalFarmContext'
import { EmptyState } from '../../components/EmptyState'
import { MetricCard } from '../../components/MetricCard'
import { PageHeader } from '../../components/PageHeader'
import { StateBanner } from '../../components/StateBanner'
import { StatusBadge } from '../../components/StatusBadge'
import { formatAlertTitle } from '../alerts/store'
import { useAuth } from '../auth/AuthProvider'
import {
  METRIC_META,
  areaLifecycleBadge,
  formatUtcToVi,
  freshnessBadge,
  receivingBadge,
} from '../measurements/format'
import { DashboardCoordinator } from './coordinator'
import {
  collectFarmActiveAlerts,
  countFarmActiveAlerts,
  findNewlyTriggeredAlerts,
  summarizeFarmEvaluation,
  summarizeNodeMonitoring,
} from './g4Helpers'

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

  const totalActiveAlerts = view ? countFarmActiveAlerts(view) : null
  const farmAlerts = view ? collectFarmActiveAlerts(view) : []
  const evalSummary = view ? summarizeFarmEvaluation(view) : null
  const focusedMonSummary = focusedNode ? summarizeNodeMonitoring(focusedNode) : null

  const [browserNotifyEnabled, setBrowserNotifyEnabled] = useState(false)
  const [notifyStatusNote, setNotifyStatusNote] = useState('')
  const seenAlertIdsRef = useRef<Set<string> | null>(null)

  useEffect(() => {
    seenAlertIdsRef.current = null
  }, [selectedFarm?.id, farmCtx?.contextGeneration])

  useEffect(() => {
    if (!view || !view.capabilities.alerts) return
    const currentOpenIds = new Set(
      farmAlerts.filter(a => a.lifecycle_status === 'OPEN').map(a => a.id),
    )
    if (seenAlertIdsRef.current === null) {
      seenAlertIdsRef.current = currentOpenIds
      return
    }
    if (
      browserNotifyEnabled &&
      typeof window !== 'undefined' &&
      'Notification' in window &&
      window.Notification.permission === 'granted'
    ) {
      const newlyTriggered = findNewlyTriggeredAlerts(seenAlertIdsRef.current, farmAlerts)
      for (const alert of newlyTriggered.slice(0, 3)) {
        try {
          new window.Notification(`Cảnh báo mới · ${alert.device_id}`, {
            body: formatAlertTitle(alert),
          })
        } catch {
          // Ignore browser Notification runtime restrictions
        }
      }
    }
    seenAlertIdsRef.current = currentOpenIds
  }, [view, farmAlerts, browserNotifyEnabled])

  async function toggleBrowserNotify() {
    if (browserNotifyEnabled) {
      setBrowserNotifyEnabled(false)
      setNotifyStatusNote('Đã tắt thông báo trình duyệt trong phiên hiện tại.')
      return
    }
    if (typeof window === 'undefined' || !('Notification' in window)) {
      setNotifyStatusNote('Trình duyệt hiện tại không hỗ trợ Web Notification API.')
      return
    }
    let perm = window.Notification.permission
    if (perm === 'default') {
      perm = await window.Notification.requestPermission()
    }
    if (perm === 'granted') {
      setBrowserNotifyEnabled(true)
      setNotifyStatusNote('Đã bật thông báo trình duyệt khi có episode OPEN mới.')
    } else {
      setBrowserNotifyEnabled(false)
      setNotifyStatusNote('Trình duyệt chưa cấp quyền Notification (hoặc đã từ chối).')
    }
  }

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
              <small className="muted">Cảnh báo hiệu lực toàn Farm</small>
              {view.capabilities.alerts && totalActiveAlerts !== null ? (
                <>
                  <div className="kpi-value tabular">{totalActiveAlerts}</div>
                  <small className="muted">
                    {farmAlerts.filter(a => a.lifecycle_status === 'OPEN').length} OPEN ·{' '}
                    {farmAlerts.filter(a => !a.acknowledged).length} chưa xác nhận
                  </small>
                </>
              ) : (
                <>
                  <div className="kpi-value muted">Chờ G4</div>
                  <small className="muted">
                    capabilities.alerts = false (chưa kích hoạt cảnh báo)
                  </small>
                </>
              )}
            </div>

            <div className="card kpi-card">
              <small className="muted">Đối soát &amp; Giám sát Farm</small>
              {view.capabilities.monitoring && evalSummary?.enabled ? (
                <>
                  <div className="kpi-value tabular">
                    {evalSummary.upToDate}/{evalSummary.total} UP_TO_DATE
                  </div>
                  <small className="muted">
                    {evalSummary.pending > 0 ? `${evalSummary.pending} PENDING · ` : ''}
                    {evalSummary.error > 0 ? `${evalSummary.error} ERROR · ` : ''}
                    Đồng bộ với worker nền G4
                  </small>
                </>
              ) : (
                <>
                  <div className="kpi-value muted">Chờ G4</div>
                  <small className="muted">
                    capabilities.monitoring = false (chưa kích hoạt giám sát)
                  </small>
                </>
              )}
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
                        {focusedMonSummary && focusedNode.monitoring ? (
                          <>
                            <StatusBadge
                              tone={focusedMonSummary.activeThresholdCount > 0 ? 'ok' : 'neutral'}
                              label={`Ngưỡng ACTIVE: ${focusedMonSummary.activeThresholdCount}/${focusedMonSummary.totalThresholdCount}`}
                            />
                            <StatusBadge
                              tone={focusedMonSummary.timeoutTone}
                              label={focusedMonSummary.timeoutLabel}
                            />
                          </>
                        ) : (
                          <StatusBadge tone="neutral" label="Giám sát: Chờ G4" />
                        )}
                        {focusedNode.evaluation ? (
                          <StatusBadge
                            tone={
                              focusedNode.evaluation.status === 'UP_TO_DATE'
                                ? 'ok'
                                : focusedNode.evaluation.status === 'PENDING'
                                  ? 'warn'
                                  : 'err'
                            }
                            label={`Đối soát: ${focusedNode.evaluation.status}`}
                          />
                        ) : (
                          <StatusBadge tone="neutral" label="Đối soát: Chờ G4" />
                        )}
                        <Link
                          className="btn btn-secondary btn-sm"
                          to={`/monitoring?node_id=${encodeURIComponent(focusedNode.profile.id)}`}
                        >
                          Cấu hình giám sát
                        </Link>
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
                <div className="stack-sm">
                  <h2>Thông báo &amp; Cảnh báo Farm</h2>
                  <small className="muted">
                    Hiển thị toàn Farm (không bị ẩn khi chọn Node ở cột trái · FL-12)
                  </small>
                </div>
                {view.capabilities.alerts ? (
                  <StatusBadge
                    tone={farmAlerts.length > 0 ? 'err' : 'ok'}
                    label={
                      farmAlerts.length > 0
                        ? `${farmAlerts.length} đang hiệu lực`
                        : '0 cảnh báo'
                    }
                  />
                ) : (
                  <StatusBadge tone="neutral" label="Chờ G4" />
                )}
              </div>

              {view.capabilities.alerts ? (
                <>
                  <div className="row-between">
                    <div className="row">
                      <Link className="btn btn-secondary btn-sm" to="/alerts">
                        Mở danh sách (/alerts)
                      </Link>
                      <Link className="btn btn-secondary btn-sm" to="/alerts?view=history">
                        Lịch sử 24h
                      </Link>
                    </div>
                    <button
                      type="button"
                      className="btn-secondary btn-sm"
                      onClick={() => void toggleBrowserNotify()}
                    >
                      {browserNotifyEnabled ? 'Tắt Web Notify' : 'Bật Web Notify'}
                    </button>
                  </div>

                  {notifyStatusNote && (
                    <small className="muted">{notifyStatusNote}</small>
                  )}

                  {farmAlerts.length === 0 ? (
                    <EmptyState
                      title="Không có cảnh báo đang hiệu lực trên Farm"
                      description="Mọi Node thuộc Farm đang ở trạng thái bình thường hoặc đã được xác nhận phục hồi."
                      action={
                        <Link className="btn btn-secondary btn-sm" to="/alerts?view=history">
                          Xem lịch sử 24h
                        </Link>
                      }
                    />
                  ) : (
                    <div className="stack-sm" role="list" aria-label="Cảnh báo toàn Farm">
                      {farmAlerts.slice(0, 12).map(alert => {
                        const isFocused = focusedNode?.profile.id === alert.node_id
                        return (
                          <div
                            key={alert.id}
                            role="listitem"
                            className="card stack-sm"
                            style={{
                              padding: '0.75rem',
                              borderColor: isFocused ? 'var(--color-accent)' : undefined,
                            }}
                          >
                            <div className="row-between">
                              <div className="row">
                                <span className="mono">
                                  <strong>{alert.device_id}</strong>
                                </span>
                                <small className="muted">Zone {alert.zone_code}</small>
                                {isFocused && (
                                  <StatusBadge tone="neutral" label="Node đang chọn" dot={false} />
                                )}
                              </div>
                              <StatusBadge
                                tone={
                                  alert.lifecycle_status === 'OPEN'
                                    ? 'err'
                                    : alert.recovery_proven === false
                                      ? 'warn'
                                      : 'ok'
                                }
                                label={
                                  alert.lifecycle_status === 'OPEN'
                                    ? 'OPEN'
                                    : 'CLOSED · Chờ phục hồi'
                                }
                              />
                            </div>
                            <strong>{formatAlertTitle(alert)}</strong>
                            <div className="row-between">
                              <small className="tabular muted">
                                Mở: {formatUtcToVi(alert.first_detected_at)}
                              </small>
                              <StatusBadge
                                tone={alert.acknowledged ? 'ok' : 'warn'}
                                label={alert.acknowledged ? 'Đã ack' : 'Chưa ack'}
                              />
                            </div>
                            <div className="row-between">
                              <small className="mono muted">#{alert.id} · rev {alert.current_revision_no}</small>
                              <Link
                                className="btn btn-secondary btn-sm"
                                to={`/alerts/${encodeURIComponent(alert.id)}`}
                              >
                                Xem &amp; Xác nhận
                              </Link>
                            </div>
                          </div>
                        )
                      })}
                      {farmAlerts.length > 12 && (
                        <small className="muted">
                          Đang hiển thị 12/{farmAlerts.length} cảnh báo mới nhất. Mở trang Cảnh báo để xem đầy đủ.
                        </small>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <EmptyState
                  title="Cảnh báo &amp; thông báo sẽ kích hoạt ở G4"
                  description="Tại thời điểm snapshot này, backend trả capabilities.alerts = false nhằm giữ ranh giới trung thực và không hiển thị số 0 giả."
                  action={
                    <Link className="btn btn-secondary btn-sm" to="/alerts">
                      Mở trang Cảnh báo
                    </Link>
                  }
                />
              )}
            </aside>
          </div>
        </>
      )}
    </div>
  )
}
