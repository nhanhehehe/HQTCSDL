import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { requestJson } from '../../api/client'
import {
  getNodeMonitoring,
  patchGatewayReceiving,
  patchNodeMonitoring,
  patchNodeReceiving,
} from '../../api/monitoring'
import type { MonitoringStateView } from '../../api/monitoring'
import { useOptionalOperationalFarm } from '../../app/OperationalFarmContext'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { EmptyState } from '../../components/EmptyState'
import { PageHeader } from '../../components/PageHeader'
import { StateBanner } from '../../components/StateBanner'
import type { BannerTone } from '../../components/StateBanner'
import { StatusBadge } from '../../components/StatusBadge'
import type { BadgeTone } from '../../components/StatusBadge'
import type { ZoneView } from '../areas/types'
import { useAuth } from '../auth/AuthProvider'
import {
  METRIC_META,
  formatUtcToVi,
  receivingBadge,
} from '../measurements/format'
import { FarmNodesLoader, isAbortOrStale } from '../measurements/scopeLoader'
import type { MetricCode, NodeChoice } from '../measurements/types'
import {
  DEFAULT_METRIC_BOUNDS,
  buildAtomicMonitoringPatchPayload,
  createDraftFromMonitoring,
  formatMonitoringApiError,
} from './store'
import type { MetricDef, MetricDraft } from './store'

type DeviceProfileDetail = {
  id: string
  device_id?: string
  gateway_id?: string
  name: string
  receiving_enabled: boolean
  row_version: string
  gateway?: { id: string; code: string } | null
  nodes?: Array<{ id: string; device_id: string; name: string }>
}

function thresholdStateBadge(state: string): { tone: BadgeTone; label: string } {
  switch (state) {
    case 'ACTIVE':
      return { tone: 'ok', label: 'Đang giám sát' }
    case 'PAUSED':
      return { tone: 'warn', label: 'Tạm dừng' }
    case 'DISABLED':
      return { tone: 'neutral', label: 'Đã tắt (giữ cặp)' }
    default:
      return { tone: 'neutral', label: 'Chưa cấu hình' }
  }
}

function evaluationBadge(status: string): { tone: BadgeTone; label: string } {
  if (status === 'UP_TO_DATE') return { tone: 'ok', label: 'Đã đối soát (UP_TO_DATE)' }
  if (status === 'PENDING') return { tone: 'warn', label: 'Đang chờ đối soát (PENDING)' }
  return { tone: 'err', label: 'Lỗi đối soát (ERROR)' }
}

export function MonitoringPage() {
  const { session } = useAuth()
  return session ? <MonitoringPanel key={session.csrf_token} csrf={session.csrf_token} /> : null
}

function MonitoringPanel({ csrf }: { csrf: string }) {
  const { canWrite } = useAuth()
  const farmCtx = useOptionalOperationalFarm()
  const selectedFarm = farmCtx?.selectedFarm ?? null
  const [searchParams, setSearchParams] = useSearchParams()
  const queryNodeId = searchParams.get('node_id') ?? ''

  const [zones, setZones] = useState<ZoneView[]>([])
  const [nodes, setNodes] = useState<NodeChoice[]>([])
  const [zoneFilter, setZoneFilter] = useState('')
  const [selectedNodeId, setSelectedNodeId] = useState(queryNodeId)
  const [metricsCatalog, setMetricsCatalog] = useState<MetricDef[]>([])

  const [monState, setMonState] = useState<MonitoringStateView | null>(null)
  const [nodeProfile, setNodeProfile] = useState<DeviceProfileDetail | null>(null)
  const [gatewayProfile, setGatewayProfile] = useState<DeviceProfileDetail | null>(null)
  const [drafts, setDrafts] = useState<Record<MetricCode, MetricDraft> | null>(null)
  const [timeoutInput, setTimeoutInput] = useState('')

  const [loadingScope, setLoadingScope] = useState(false)
  const [loadingNode, setLoadingNode] = useState(false)
  const [busyMutation, setBusyMutation] = useState(false)
  const [banner, setBanner] = useState<{ tone: BannerTone; message: string } | null>(null)
  const [confirmGwToggle, setConfirmGwToggle] = useState<boolean | null>(null)

  const loaderRef = useRef(new FarmNodesLoader())
  const nodeGenRef = useRef(0)
  const nodeCtrlRef = useRef<AbortController | null>(null)
  const operationRef = useRef(false)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    const loader = loaderRef.current
    return () => {
      mountedRef.current = false
      loader.dispose()
      nodeCtrlRef.current?.abort()
    }
  }, [])

  // Load read-only metric catalog once
  useEffect(() => {
    const ctrl = new AbortController()
    requestJson<{ items: MetricDef[] }>('/api/v1/metrics', { signal: ctrl.signal })
      .then(res => {
        if (mountedRef.current && !ctrl.signal.aborted) {
          setMetricsCatalog(res.items)
        }
      })
      .catch(() => {})
    return () => ctrl.abort()
  }, [])

  // Load Farm Zones & Nodes when Farm changes
  useEffect(() => {
    const farmId = selectedFarm?.id ?? ''
    nodeGenRef.current += 1
    nodeCtrlRef.current?.abort()
    setZones([])
    setNodes([])
    setZoneFilter('')
    setMonState(null)
    setNodeProfile(null)
    setGatewayProfile(null)
    setDrafts(null)
    setTimeoutInput('')
    setBanner(null)

    if (!farmId) {
      setSelectedNodeId('')
      return
    }

    setLoadingScope(true)
    void loaderRef.current
      .load(farmId)
      .then(result => {
        if (!result || !mountedRef.current) return
        setZones(result.zones)
        setNodes(result.nodes)
        const nextId =
          queryNodeId && result.nodes.some(n => n.id === queryNodeId)
            ? queryNodeId
            : (result.nodes[0]?.id ?? '')
        setSelectedNodeId(nextId)
      })
      .catch(error => {
        if (isAbortOrStale(error) || !mountedRef.current) return
        setBanner({
          tone: 'err',
          message: formatMonitoringApiError(error, 'Không tải được danh sách Node của Farm.'),
        })
      })
      .finally(() => {
        if (mountedRef.current) setLoadingScope(false)
      })
  }, [selectedFarm?.id, farmCtx?.contextGeneration, queryNodeId])

  const loadSelectedNodeMonitoring = useCallback(async (nodeId: string) => {
    nodeCtrlRef.current?.abort()
    const activeGen = ++nodeGenRef.current
    if (!nodeId) {
      setMonState(null)
      setNodeProfile(null)
      setGatewayProfile(null)
      setDrafts(null)
      setTimeoutInput('')
      return
    }

    const ctrl = new AbortController()
    nodeCtrlRef.current = ctrl
    setLoadingNode(true)

    try {
      const [mon, nodeDet] = await Promise.all([
        getNodeMonitoring(nodeId, ctrl.signal),
        requestJson<DeviceProfileDetail>(`/api/v1/nodes/${encodeURIComponent(nodeId)}`, {
          signal: ctrl.signal,
        }),
      ])
      if (ctrl.signal.aborted || activeGen !== nodeGenRef.current || !mountedRef.current) return

      let gwDet: DeviceProfileDetail | null = null
      if (nodeDet.gateway?.id) {
        gwDet = await requestJson<DeviceProfileDetail>(
          `/api/v1/gateways/${encodeURIComponent(nodeDet.gateway.id)}`,
          { signal: ctrl.signal },
        )
        if (ctrl.signal.aborted || activeGen !== nodeGenRef.current || !mountedRef.current) return
      }

      const initialDraft = createDraftFromMonitoring(mon)
      setMonState(mon)
      setNodeProfile(nodeDet)
      setGatewayProfile(gwDet)
      setDrafts(initialDraft.thresholds)
      setTimeoutInput(initialDraft.timeoutMinutes)
    } catch (error) {
      if (isAbortOrStale(error) || !mountedRef.current || activeGen !== nodeGenRef.current) return
      setBanner({
        tone: 'err',
        message: formatMonitoringApiError(error, 'Không tải được cấu hình giám sát của Node.'),
      })
    } finally {
      if (mountedRef.current && activeGen === nodeGenRef.current) {
        setLoadingNode(false)
      }
    }
  }, [])

  useEffect(() => {
    void loadSelectedNodeMonitoring(selectedNodeId)
  }, [selectedNodeId, loadSelectedNodeMonitoring])

  const visibleNodes = zoneFilter ? nodes.filter(n => n.zone.id === zoneFilter) : nodes

  function handleSelectNode(nextNodeId: string) {
    setSelectedNodeId(nextNodeId)
    setBanner(null)
    if (nextNodeId) {
      setSearchParams({ node_id: nextNodeId }, { replace: true })
    } else {
      setSearchParams({}, { replace: true })
    }
  }

  async function saveMonitoring(includeThresholds: boolean, includeTimeout: boolean) {
    if (!monState || !drafts || operationRef.current || !canWrite()) return
    const built = buildAtomicMonitoringPatchPayload({
      includeThresholds,
      includeTimeout,
      mon: monState,
      drafts,
      timeoutInput,
      metricsCatalog,
    })
    if (!built.ok) {
      setBanner({ tone: 'err', message: built.error })
      return
    }

    operationRef.current = true
    setBusyMutation(true)
    setBanner(null)
    try {
      const updated = await patchNodeMonitoring(monState.node_id, built.payload, csrf)
      if (!mountedRef.current) return
      const nextDraft = createDraftFromMonitoring(updated)
      setMonState(updated)
      setDrafts(nextDraft.thresholds)
      setTimeoutInput(nextDraft.timeoutMinutes)
      setBanner({
        tone: updated.evaluation.status === 'PENDING' ? 'warn' : 'ok',
        message:
          updated.evaluation.status === 'PENDING'
            ? 'Đã lưu cấu hình — trạng thái đối soát cảnh báo: PENDING (chờ worker xử lý).'
            : 'Đã lưu cấu hình giám sát thành công.',
      })
    } catch (error) {
      if (!isAbortOrStale(error) && mountedRef.current) {
        setBanner({
          tone: 'err',
          message: formatMonitoringApiError(error, 'Lưu cấu hình giám sát thất bại.'),
        })
      }
    } finally {
      operationRef.current = false
      if (mountedRef.current) setBusyMutation(false)
    }
  }

  async function toggleNodeReceiving(nextEnabled: boolean) {
    if (!nodeProfile || operationRef.current || !canWrite()) return
    operationRef.current = true
    setBusyMutation(true)
    setBanner(null)
    try {
      const res = await patchNodeReceiving(
        nodeProfile.id,
        { receiving_enabled: nextEnabled, expected_row_version: nodeProfile.row_version },
        csrf,
      )
      if (!mountedRef.current) return
      setNodeProfile(prev =>
        prev
          ? {
              ...prev,
              receiving_enabled: res.receiving_enabled,
              row_version: res.profile_row_version,
            }
          : prev,
      )
      setMonState(res.monitoring)
      setBanner({
        tone: 'ok',
        message: res.receiving_enabled
          ? 'Đã bật quyền tiếp nhận dữ liệu mới cho Node.'
          : 'Đã tắt quyền tiếp nhận dữ liệu mới cho Node (chỉ chặn lưu mới tại Backend).',
      })
    } catch (error) {
      if (!isAbortOrStale(error) && mountedRef.current) {
        setBanner({
          tone: 'err',
          message: formatMonitoringApiError(error, 'Đổi quyền tiếp nhận Node thất bại.'),
        })
      }
    } finally {
      operationRef.current = false
      if (mountedRef.current) setBusyMutation(false)
    }
  }

  async function toggleGatewayReceiving(nextEnabled: boolean) {
    if (!gatewayProfile || operationRef.current || !canWrite()) return
    operationRef.current = true
    setBusyMutation(true)
    setConfirmGwToggle(null)
    setBanner(null)
    try {
      const res = await patchGatewayReceiving(
        gatewayProfile.id,
        { receiving_enabled: nextEnabled, expected_row_version: gatewayProfile.row_version },
        csrf,
      )
      if (!mountedRef.current) return
      setGatewayProfile(prev =>
        prev
          ? {
              ...prev,
              receiving_enabled: res.receiving_enabled,
              row_version: res.profile_row_version,
            }
          : prev,
      )
      const affected = res.affected_nodes.find(n => n.node_id === selectedNodeId)
      if (affected) setMonState(affected)
      setBanner({
        tone: 'ok',
        message: res.receiving_enabled
          ? `Đã bật quyền tiếp nhận cho Gateway (${res.affected_nodes.length} Node liên kết được cập nhật).`
          : `Đã tắt quyền tiếp nhận cho Gateway (${res.affected_nodes.length} Node liên kết tạm dừng giám sát).`,
      })
    } catch (error) {
      if (!isAbortOrStale(error) && mountedRef.current) {
        setBanner({
          tone: 'err',
          message: formatMonitoringApiError(error, 'Đổi quyền tiếp nhận Gateway thất bại.'),
        })
      }
    } finally {
      operationRef.current = false
      if (mountedRef.current) setBusyMutation(false)
    }
  }

  const catalogByCode = new Map(metricsCatalog.map(m => [m.code, m]))
  const timeoutBlockedByProfile = monState?.timeout_validation.state === 'PROFILE_MISSING'

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Cấu hình & Quản trị · G4 · FL-04 & FL-08"
        title="Giám sát, Ngưỡng & Quyền tiếp nhận"
        description="Bật/tắt quyền lưu dữ liệu mới theo Node/Gateway và cấu hình độc lập hoặc nguyên tử ngưỡng 5 đại lượng cùng timeout T."
        actions={
          selectedNodeId && (
            <button
              type="button"
              className="btn-secondary btn-sm"
              disabled={loadingNode || busyMutation}
              onClick={() => void loadSelectedNodeMonitoring(selectedNodeId)}
            >
              Tải lại Node
            </button>
          )
        }
      />

      {!selectedFarm && (
        <StateBanner
          tone="warn"
          message="Chưa chọn Farm vận hành. Vui lòng chọn tường minh một Farm từ bộ chọn trên Topbar."
        />
      )}

      {banner && <StateBanner tone={banner.tone} message={banner.message} />}

      {selectedFarm && (
        <section className="card stack-sm" aria-label="Phạm vi Node giám sát">
          <div className="row">
            <label className="filter-field">
              Lọc Zone
              <select
                value={zoneFilter}
                disabled={loadingScope || busyMutation}
                onChange={e => {
                  const nextZ = e.target.value
                  setZoneFilter(nextZ)
                  const filtered = nextZ ? nodes.filter(n => n.zone.id === nextZ) : nodes
                  if (!filtered.some(n => n.id === selectedNodeId)) {
                    handleSelectNode(filtered[0]?.id ?? '')
                  }
                }}
              >
                <option value="">Tất cả Zone ({zones.length})</option>
                {zones.map(z => (
                  <option key={z.id} value={z.id}>
                    {z.code} · {z.name}
                    {z.archived_at ? ' (Đã lưu trữ)' : ''}
                  </option>
                ))}
              </select>
            </label>

            <label className="filter-field" style={{ minWidth: '280px' }}>
              Node cấu hình
              <select
                value={selectedNodeId}
                disabled={loadingScope || busyMutation || visibleNodes.length === 0}
                onChange={e => handleSelectNode(e.target.value)}
              >
                {visibleNodes.length === 0 && <option value="">Không có Node</option>}
                {visibleNodes.map(n => (
                  <option key={n.id} value={n.id}>
                    {n.device_id} · {n.name} ({n.zone.code})
                  </option>
                ))}
              </select>
            </label>
          </div>
        </section>
      )}

      {selectedFarm && !loadingScope && visibleNodes.length === 0 && (
        <EmptyState
          title="Chưa có Node trong phạm vi chọn"
          description="Hãy chọn Zone khác hoặc đăng ký Node ở mục Thiết bị & Khóa."
        />
      )}

      {loadingNode && <p className="muted">Đang tải cấu hình giám sát…</p>}

      {monState && nodeProfile && drafts && (
        <>
          {/* KHỐI A: Quyền tiếp nhận dữ liệu mới (FL-04) */}
          <section className="card stack" aria-label="Quyền tiếp nhận dữ liệu mới">
            <div className="card-header">
              <div className="stack-sm">
                <h2>Khối A: Quyền tiếp nhận dữ liệu mới (FL-04)</h2>
                <small className="muted">
                  Tắt quyền chỉ chặn Backend lưu bản mới, không gửi lệnh điều khiển tới phần cứng IoT.
                </small>
              </div>
              <StatusBadge
                tone={evaluationBadge(monState.evaluation.status).tone}
                label={evaluationBadge(monState.evaluation.status).label}
              />
            </div>

            <div className="kv-grid">
              <div className="kv-item">
                <dt>Node ({nodeProfile.device_id})</dt>
                <dd className="row">
                  <StatusBadge
                    tone={receivingBadge(nodeProfile.receiving_enabled).tone}
                    label={receivingBadge(nodeProfile.receiving_enabled).label}
                  />
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    disabled={busyMutation || !canWrite()}
                    onClick={() => void toggleNodeReceiving(!nodeProfile.receiving_enabled)}
                  >
                    {nodeProfile.receiving_enabled ? 'Tắt quyền Node' : 'Bật quyền Node'}
                  </button>
                </dd>
              </div>

              <div className="kv-item">
                <dt>
                  Gateway liên kết ({gatewayProfile?.gateway_id ?? 'Chưa gán Gateway'})
                </dt>
                <dd className="row">
                  {gatewayProfile ? (
                    <>
                      <StatusBadge
                        tone={receivingBadge(gatewayProfile.receiving_enabled).tone}
                        label={receivingBadge(gatewayProfile.receiving_enabled).label}
                      />
                      <button
                        type="button"
                        className="btn-secondary btn-sm"
                        disabled={busyMutation || !canWrite()}
                        onClick={() => setConfirmGwToggle(!gatewayProfile.receiving_enabled)}
                      >
                        {gatewayProfile.receiving_enabled
                          ? 'Tắt quyền Gateway'
                          : 'Bật quyền Gateway'}
                      </button>
                    </>
                  ) : (
                    <span className="muted">Node chưa liên kết Gateway</span>
                  )}
                </dd>
              </div>
            </div>
          </section>

          {/* KHỐI B: Giám sát Timeout riêng Node (FL-08) */}
          <section className="card stack" aria-label="Giám sát Timeout Node">
            <div className="card-header">
              <div className="stack-sm">
                <h2>Khối B: Giám sát Timeout riêng Node (FL-08)</h2>
                <small className="muted">
                  Mốc S: {formatUtcToVi(monState.S)} · Mốc L: {formatUtcToVi(monState.L)}
                </small>
              </div>
              <StatusBadge
                tone={
                  monState.timeout_state === 'ACTIVE'
                    ? 'ok'
                    : monState.timeout_state === 'PAUSED'
                      ? 'warn'
                      : 'neutral'
                }
                label={`Timeout: ${monState.timeout_state}`}
              />
            </div>

            {timeoutBlockedByProfile && (
              <StateBanner
                tone="warn"
                message="PROFILE_MISSING: Thiếu profile thời gian của Node trong cấu hình Backend. Khóa đổi T nhưng vẫn giữ nguyên T hiện có và vẫn cho phép chỉnh sửa ngưỡng."
              />
            )}

            {monState.timeout_validation.state === 'TIMEOUT_PROFILE_INCOMPATIBLE' && (
              <StateBanner
                tone="warn"
                message={`TIMEOUT_PROFILE_INCOMPATIBLE: T hiện tại không đạt tối thiểu ${monState.timeout_validation.min_timeout_minutes} phút theo profile mới.`}
              />
            )}

            <div className="row-between">
              <label className="filter-field">
                Thời gian timeout T (phút)
                <input
                  type="text"
                  inputMode="numeric"
                  disabled={busyMutation || timeoutBlockedByProfile}
                  value={timeoutInput}
                  onChange={e => setTimeoutInput(e.target.value)}
                  placeholder={
                    monState.timeout_validation.min_timeout_minutes !== null
                      ? `Tối thiểu ${monState.timeout_validation.min_timeout_minutes}`
                      : 'Số nguyên dương'
                  }
                />
              </label>

              <div className="row">
                {monState.timeout_validation.min_timeout_minutes !== null && (
                  <small className="muted tabular">
                    Tối thiểu theo profile: {monState.timeout_validation.min_timeout_minutes} phút (chu
                    kỳ {monState.timeout_validation.sampling_interval_seconds}s + budget{' '}
                    {monState.timeout_validation.normal_delivery_budget_seconds}s)
                  </small>
                )}
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busyMutation || !canWrite() || timeoutBlockedByProfile}
                  onClick={() => void saveMonitoring(false, true)}
                >
                  Lưu riêng Timeout T
                </button>
              </div>
            </div>
          </section>

          {/* KHỐI C: Ngưỡng cảnh báo 5 đại lượng (FL-08) */}
          <section className="card stack" aria-label="Ngưỡng cảnh báo 5 đại lượng">
            <div className="card-header">
              <div className="stack-sm">
                <h2>Khối C: Ngưỡng cảnh báo 5 đại lượng (FL-08)</h2>
                <small className="muted">
                  Tắt công tắc bật giữ nguyên cặp ngưỡng dưới/trên. Chấp nhận tối đa 2 chữ số thập
                  phân chính xác (`DECIMAL(9,2)`).
                </small>
              </div>
              <small className="mono muted">
                Threshold Config #{monState.threshold_config_id ?? '—'} · Timeout Config #
                {monState.timeout_config_id ?? '—'}
              </small>
            </div>

            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Đại lượng</th>
                    <th>Bật</th>
                    <th>Ngưỡng dưới</th>
                    <th>Ngưỡng trên</th>
                    <th>Miền kỹ thuật</th>
                    <th>Trạng thái</th>
                  </tr>
                </thead>
                <tbody>
                  {METRIC_META.map(meta => {
                    const rowDraft = drafts[meta.code]
                    const viewItem = monState.thresholds.find(t => t.metric_code === meta.code)
                    const cat = catalogByCode.get(meta.code)
                    const fallback = DEFAULT_METRIC_BOUNDS[meta.code]
                    const minStr = cat?.min_value ?? fallback.min
                    const maxStr = cat?.max_value ?? fallback.max
                    const unit = cat?.unit ?? meta.unit
                    const stBadge = thresholdStateBadge(viewItem?.state ?? 'UNCONFIGURED')
                    return (
                      <tr key={meta.code}>
                        <td>
                          <strong>{meta.label}</strong>
                          <div className="mono muted">{meta.code}</div>
                        </td>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Bật ngưỡng ${meta.code}`}
                            checked={rowDraft.enabled}
                            disabled={busyMutation}
                            onChange={e =>
                              setDrafts(prev =>
                                prev
                                  ? {
                                      ...prev,
                                      [meta.code]: { ...prev[meta.code], enabled: e.target.checked },
                                    }
                                  : prev,
                              )
                            }
                          />
                        </td>
                        <td>
                          <input
                            type="text"
                            aria-label={`Ngưỡng dưới ${meta.code}`}
                            className="tabular"
                            disabled={busyMutation}
                            value={rowDraft.lower}
                            placeholder="—"
                            onChange={e =>
                              setDrafts(prev =>
                                prev
                                  ? {
                                      ...prev,
                                      [meta.code]: { ...prev[meta.code], lower: e.target.value },
                                    }
                                  : prev,
                              )
                            }
                          />
                        </td>
                        <td>
                          <input
                            type="text"
                            aria-label={`Ngưỡng trên ${meta.code}`}
                            className="tabular"
                            disabled={busyMutation}
                            value={rowDraft.upper}
                            placeholder="—"
                            onChange={e =>
                              setDrafts(prev =>
                                prev
                                  ? {
                                      ...prev,
                                      [meta.code]: { ...prev[meta.code], upper: e.target.value },
                                    }
                                  : prev,
                              )
                            }
                          />
                        </td>
                        <td className="tabular muted">
                          {minStr} .. {maxStr} {unit}
                        </td>
                        <td>
                          <StatusBadge tone={stBadge.tone} label={stBadge.label} />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            <div className="row-between">
              <small className="muted">
                Generation: {monState.evaluation.completed_generation} /{' '}
                {monState.evaluation.desired_generation}
                {monState.evaluation.last_error_code
                  ? ` · Lỗi gần nhất: ${monState.evaluation.last_error_code}`
                  : ''}
              </small>
              <div className="row">
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busyMutation || !canWrite()}
                  onClick={() => void saveMonitoring(true, false)}
                >
                  Lưu nhóm Ngưỡng
                </button>
                <button
                  type="button"
                  disabled={busyMutation || !canWrite() || timeoutBlockedByProfile}
                  onClick={() => void saveMonitoring(true, true)}
                >
                  Lưu tất cả nguyên tử
                </button>
              </div>
            </div>
          </section>
        </>
      )}

      <ConfirmDialog
        open={confirmGwToggle !== null}
        title={
          confirmGwToggle
            ? 'Xác nhận bật quyền tiếp nhận Gateway'
            : 'Xác nhận tắt quyền tiếp nhận Gateway'
        }
        description={
          <p>
            {gatewayProfile
              ? `Thao tác này áp dụng nguyên tử cho Gateway ${gatewayProfile.gateway_id} và toàn bộ ${gatewayProfile.nodes?.length ?? 0} Node liên kết.`
              : ''}
          </p>
        }
        confirmLabel={confirmGwToggle ? 'Bật quyền Gateway' : 'Tắt quyền Gateway'}
        tone={confirmGwToggle === false ? 'danger' : 'primary'}
        busy={busyMutation}
        onCancel={() => setConfirmGwToggle(null)}
        onConfirm={() => {
          if (confirmGwToggle !== null) void toggleGatewayReceiving(confirmGwToggle)
        }}
      />
    </div>
  )
}
