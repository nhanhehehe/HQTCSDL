import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { AlertKind, AlertLifecycle, AlertSummaryView } from '../../api/alerts'
import { useOptionalOperationalFarm } from '../../app/OperationalFarmContext'
import { CursorPager } from '../../components/CursorPager'
import { DataTable } from '../../components/DataTable'
import type { Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { FilterBar } from '../../components/FilterBar'
import { PageHeader } from '../../components/PageHeader'
import { StateBanner } from '../../components/StateBanner'
import { StatusBadge } from '../../components/StatusBadge'
import { formatUtcToVi } from '../measurements/format'
import { FarmNodesLoader, isAbortOrStale } from '../measurements/scopeLoader'
import type { NodeChoice } from '../measurements/types'
import {
  AlertsStore,
  buildAlertsListParams,
  createDefaultAlertFilterDraft,
  formatAlertTitle,
  formatEndReason,
  summarizeEvaluationMap,
  validateAlertFilterDraft,
} from './store'
import type { AlertFilterDraft, AlertsStoreState } from './store'

function readInitialDraftFromSearch(search: URLSearchParams): AlertFilterDraft {
  const viewParam = search.get('view')
  const view: 'active' | 'history' = viewParam === 'history' ? 'history' : 'active'
  const draft = createDefaultAlertFilterDraft(view, search.get('node_id') ?? '')
  const kind = search.get('kind')
  if (kind === 'THRESHOLD' || kind === 'TIMEOUT') draft.kind = kind
  const lifecycle = search.get('lifecycle')
  if (lifecycle === 'OPEN' || (lifecycle === 'CLOSED' && view === 'history')) {
    draft.lifecycle = lifecycle
  }
  const ack = search.get('acknowledged')
  if (ack === 'true' || ack === 'false') draft.acknowledged = ack
  const rec = search.get('recovery_proven')
  if ((rec === 'true' || rec === 'false') && draft.kind !== 'TIMEOUT') {
    draft.recovery_proven = rec
  }
  return draft
}

function lifecycleBadge(item: AlertSummaryView) {
  if (item.lifecycle_status === 'OPEN') {
    return <StatusBadge tone="err" label="OPEN" />
  }
  if (item.recovery_proven === false) {
    return <StatusBadge tone="warn" label="CLOSED · Chưa xác nhận phục hồi" />
  }
  return <StatusBadge tone="ok" label="CLOSED · Đã phục hồi" />
}

export function AlertsPage() {
  const farmCtx = useOptionalOperationalFarm()
  const farms = farmCtx?.farms ?? []
  const selectedFarmId = farmCtx?.selectedFarmId ?? ''
  const selectedFarm = farmCtx?.selectedFarm ?? null
  const farmsLoading = farmCtx?.loading ?? false
  const farmsError = farmCtx?.error ?? ''
  const contextGeneration = farmCtx?.contextGeneration ?? 0
  const [searchParams] = useSearchParams()

  const [draft, setDraft] = useState<AlertFilterDraft>(() =>
    readInitialDraftFromSearch(searchParams),
  )
  const [appliedDraft, setAppliedDraft] = useState<AlertFilterDraft>(() =>
    readInitialDraftFromSearch(searchParams),
  )
  const [validationError, setValidationError] = useState('')
  const [nodes, setNodes] = useState<NodeChoice[]>([])
  const [nodesLoading, setNodesLoading] = useState(false)
  const [nodesError, setNodesError] = useState('')

  const storeRef = useRef<AlertsStore | null>(null)
  if (!storeRef.current) {
    storeRef.current = new AlertsStore()
  }
  const [storeState, setStoreState] = useState<AlertsStoreState>(() =>
    storeRef.current!.getState(),
  )

  const nodeLoaderRef = useRef(new FarmNodesLoader())
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    const store = storeRef.current!
    const nodeLoader = nodeLoaderRef.current
    const unsub = store.subscribe(next => {
      if (mountedRef.current) setStoreState(next)
    })
    return () => {
      mountedRef.current = false
      unsub()
      store.dispose()
      nodeLoader.dispose()
    }
  }, [])

  // Load nodes when Farm changes
  useEffect(() => {
    const loader = nodeLoaderRef.current
    if (!selectedFarmId) {
      setNodes([])
      setNodesError('')
      storeRef.current?.reset()
      return
    }
    setNodesLoading(true)
    setNodesError('')
    void loader
      .load(selectedFarmId)
      .then(res => {
        if (!mountedRef.current || !res) return
        const list = res.nodes
        setNodes(list)
        setNodesLoading(false)
        setDraft(prev =>
          prev.node_id && !list.some(n => n.id === prev.node_id)
            ? { ...prev, node_id: '' }
            : prev,
        )
        setAppliedDraft(prev =>
          prev.node_id && !list.some(n => n.id === prev.node_id)
            ? { ...prev, node_id: '' }
            : prev,
        )
      })
      .catch(error => {
        if (!mountedRef.current || isAbortOrStale(error)) return
        setNodesLoading(false)
        setNodesError('Không tải được danh sách Node thuộc Farm đang chọn.')
      })
  }, [selectedFarmId, contextGeneration])

  const executeLoad = useCallback(
    (farmId: string, nextDraft: AlertFilterDraft) => {
      const check = validateAlertFilterDraft(nextDraft)
      if (!check.ok) {
        setValidationError(check.error)
        return
      }
      setValidationError('')
      const params = buildAlertsListParams(farmId, nextDraft, check, { pageSize: 20 })
      void storeRef.current?.load(params)
    },
    [],
  )

  // Initial load and reload when Farm or appliedDraft changes
  useEffect(() => {
    if (!selectedFarmId) return
    executeLoad(selectedFarmId, appliedDraft)
  }, [selectedFarmId, contextGeneration, appliedDraft, executeLoad])

  function handleTabChange(nextView: 'active' | 'history') {
    const next: AlertFilterDraft = {
      ...draft,
      view: nextView,
      lifecycle: nextView === 'active' && draft.lifecycle === 'CLOSED' ? '' : draft.lifecycle,
      fromLocal: nextView === 'active' ? '' : draft.fromLocal,
      toLocal: nextView === 'active' ? '' : draft.toLocal,
    }
    setDraft(next)
    setAppliedDraft(next)
  }

  function handleKindChange(nextKind: '' | AlertKind) {
    setDraft(prev => ({
      ...prev,
      kind: nextKind,
      recovery_proven: nextKind === 'TIMEOUT' ? '' : prev.recovery_proven,
    }))
  }

  function handleApplyFilters() {
    const check = validateAlertFilterDraft(draft)
    if (!check.ok) {
      setValidationError(check.error)
      return
    }
    setValidationError('')
    setAppliedDraft({ ...draft })
  }

  function handleResetFilters() {
    const clean = createDefaultAlertFilterDraft(draft.view)
    setDraft(clean)
    setAppliedDraft(clean)
    setValidationError('')
  }

  const evalSummary = useMemo(
    () => summarizeEvaluationMap(storeState.evaluationByNode),
    [storeState.evaluationByNode],
  )

  const columns: Column<AlertSummaryView>[] = [
    {
      key: 'id',
      header: 'Episode',
      render: item => (
        <div className="stack-sm">
          <Link to={`/alerts/${encodeURIComponent(item.id)}`} className="mono">
            <strong>#{item.id}</strong>
          </Link>
          {!item.is_current && <StatusBadge tone="neutral" label="Đã thay thế (Retire)" />}
        </div>
      ),
    },
    {
      key: 'node',
      header: 'Node / Zone',
      render: item => (
        <div className="stack-sm">
          <span>
            <span className="mono">{item.device_id}</span> · {item.node_name}
          </span>
          <small className="muted mono">{item.zone_code}</small>
        </div>
      ),
    },
    {
      key: 'title',
      header: 'Loại & Đại lượng',
      render: item => (
        <div className="stack-sm">
          <strong>{formatAlertTitle(item)}</strong>
          <small className="muted tabular">
            Rev {item.current_revision_no} · Ack v{item.acknowledgement_version}
          </small>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Vòng đời & Phục hồi',
      render: item => (
        <div className="stack-sm">
          {lifecycleBadge(item)}
          {item.end_reason && (
            <small className="muted">{formatEndReason(item.end_reason)}</small>
          )}
        </div>
      ),
    },
    {
      key: 'ack',
      header: 'Xác nhận (Ack)',
      render: item =>
        item.acknowledged && item.current_acknowledgement ? (
          <div className="stack-sm">
            <StatusBadge tone="ok" label="Đã xác nhận" />
            <small className="muted tabular">
              {item.current_acknowledgement.admin_username ?? 'Admin'} ·{' '}
              {formatUtcToVi(item.current_acknowledgement.acknowledged_at)}
            </small>
          </div>
        ) : (
          <StatusBadge tone="warn" label="Chưa xác nhận" />
        ),
    },
    {
      key: 'timestamps',
      header: 'Phát hiện / Kết thúc',
      className: 'tabular',
      render: item => (
        <div className="stack-sm">
          <small>Mở: {formatUtcToVi(item.first_detected_at)}</small>
          <small className="muted">
            Đóng: {item.closed_at ? formatUtcToVi(item.closed_at) : 'Đang mở'}
          </small>
        </div>
      ),
    },
    {
      key: 'action',
      header: 'Chi tiết',
      render: item => (
        <Link
          className="btn btn-secondary btn-sm"
          to={`/alerts/${encodeURIComponent(item.id)}`}
        >
          Mở episode
        </Link>
      ),
    },
  ]

  return (
    <div className="stack" aria-label="Cảnh báo và Lịch sử sự cố">
      <PageHeader
        eyebrow="Vận hành · FL-11 (G4)"
        title="Cảnh báo & Lịch sử episode"
        description={
          selectedFarm
            ? `Theo dõi các episode cảnh báo của Farm ${selectedFarm.code} (${selectedFarm.name}) theo thời gian thực và audit 24 giờ gần nhất.`
            : 'Chọn Farm trên thanh điều hướng để xem cảnh báo đang hiệu lực và lịch sử episode.'
        }
        actions={
          <div className="row">
            <button
              type="button"
              className="btn-secondary"
              disabled={!selectedFarmId || storeState.loading}
              onClick={() => selectedFarmId && executeLoad(selectedFarmId, appliedDraft)}
            >
              {storeState.loading ? 'Đang tải…' : 'Làm mới danh sách'}
            </button>
          </div>
        }
      />

      {farmsError && <StateBanner tone="err" message={farmsError} />}
      {nodesError && <StateBanner tone="warn" message={nodesError} />}
      {validationError && <StateBanner tone="err" message={validationError} />}
      {storeState.error && <StateBanner tone="err" message={storeState.error} />}

      {!farmsLoading && farms.length === 0 ? (
        <EmptyState
          title="Chưa có Farm đang hoạt động"
          description="Tạo Farm tại mục Khu vực (Farm/Zone) trước khi theo dõi cảnh báo."
          action={
            <Link className="btn btn-secondary btn-sm" to="/areas">
              Đi tới Khu vực
            </Link>
          }
        />
      ) : (
        <>
          {/* Tabs theo FL-11 */}
          <div className="tabs" role="tablist" aria-label="Chế độ xem cảnh báo">
            <button
              type="button"
              role="tab"
              aria-selected={draft.view === 'active'}
              className={`tab-btn${draft.view === 'active' ? ' active' : ''}`}
              onClick={() => handleTabChange('active')}
            >
              Đang hiệu lực (OPEN &amp; Chưa phục hồi)
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={draft.view === 'history'}
              className={`tab-btn${draft.view === 'history' ? ' active' : ''}`}
              onClick={() => handleTabChange('history')}
            >
              Lịch sử episode (Mặc định 24h / Tùy chỉnh)
            </button>
          </div>

          {/* Tóm tắt trạng thái đánh giá và cửa sổ truy vấn */}
          <div className="kpi-grid" aria-label="Tóm tắt đối soát cảnh báo">
            <div className="card kpi-card">
              <small className="muted">Thời điểm truy vấn (as_of)</small>
              <strong className="tabular">
                {storeState.asOf ? formatUtcToVi(storeState.asOf) : '—'}
              </strong>
              <small className="muted">Hiển thị theo giờ Việt Nam (+07:00)</small>
            </div>

            <div className="card kpi-card">
              <small className="muted">Cửa sổ dữ liệu (range)</small>
              <strong className="tabular">
                {storeState.range.from || storeState.range.to
                  ? `${storeState.range.from ? formatUtcToVi(storeState.range.from) : '…'} → ${
                      storeState.range.to ? formatUtcToVi(storeState.range.to) : '…'
                    }`
                  : draft.view === 'active'
                    ? 'Tất cả episode đang hiệu lực'
                    : '24 giờ gần nhất'}
              </strong>
              <small className="muted">Phạm vi theo hợp đồng FL-11</small>
            </div>

            <div className="card kpi-card">
              <small className="muted">Độ mới đối soát trên Farm (evaluation_by_node)</small>
              <div className="row">
                <StatusBadge tone="ok" label={`UP_TO_DATE: ${evalSummary.upToDate}`} />
                {evalSummary.pending > 0 && (
                  <StatusBadge tone="warn" label={`PENDING: ${evalSummary.pending}`} />
                )}
                {evalSummary.error > 0 && (
                  <StatusBadge tone="err" label={`ERROR: ${evalSummary.error}`} />
                )}
              </div>
              <small className="muted">
                Tổng {evalSummary.total} Node thuộc phạm vi đánh giá
              </small>
            </div>
          </div>

          {/* Bộ lọc FL-11 */}
          <div className="card stack">
            <FilterBar>
              <label>
                Node
                <select
                  aria-label="Lọc theo Node"
                  disabled={nodesLoading}
                  value={draft.node_id}
                  onChange={e => setDraft(prev => ({ ...prev, node_id: e.target.value }))}
                >
                  <option value="">Tất cả Node thuộc Farm</option>
                  {nodes.map(n => (
                    <option key={n.id} value={n.id}>
                      {n.device_id} · {n.name} ({n.zone.code})
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Loại (kind)
                <select
                  aria-label="Lọc theo loại cảnh báo"
                  value={draft.kind}
                  onChange={e => handleKindChange(e.target.value as '' | AlertKind)}
                >
                  <option value="">Tất cả</option>
                  <option value="THRESHOLD">Ngưỡng (THRESHOLD)</option>
                  <option value="TIMEOUT">Quá hạn (TIMEOUT)</option>
                </select>
              </label>

              <label>
                Vòng đời (lifecycle)
                <select
                  aria-label="Lọc theo lifecycle"
                  value={draft.lifecycle}
                  onChange={e =>
                    setDraft(prev => ({
                      ...prev,
                      lifecycle: e.target.value as '' | AlertLifecycle,
                    }))
                  }
                >
                  <option value="">Tất cả</option>
                  <option value="OPEN">OPEN</option>
                  {draft.view === 'history' && <option value="CLOSED">CLOSED</option>}
                </select>
              </label>

              <label>
                Xác nhận (acknowledged)
                <select
                  aria-label="Lọc theo trạng thái xác nhận"
                  value={draft.acknowledged}
                  onChange={e =>
                    setDraft(prev => ({
                      ...prev,
                      acknowledged: e.target.value as '' | 'true' | 'false',
                    }))
                  }
                >
                  <option value="">Tất cả</option>
                  <option value="false">Chưa xác nhận (false)</option>
                  <option value="true">Đã xác nhận (true)</option>
                </select>
              </label>

              <label>
                Phục hồi (recovery_proven)
                <select
                  aria-label="Lọc theo recovery_proven"
                  disabled={draft.kind === 'TIMEOUT'}
                  value={draft.recovery_proven}
                  onChange={e =>
                    setDraft(prev => ({
                      ...prev,
                      recovery_proven: e.target.value as '' | 'true' | 'false',
                    }))
                  }
                >
                  <option value="">Tất cả</option>
                  <option value="false">Chưa chứng minh phục hồi (false)</option>
                  <option value="true">Đã phục hồi (true)</option>
                </select>
              </label>

              {draft.view === 'history' && (
                <>
                  <label>
                    Từ (+07:00)
                    <input
                      type="datetime-local"
                      aria-label="Mốc Từ (+07:00)"
                      value={draft.fromLocal}
                      onChange={e => setDraft(prev => ({ ...prev, fromLocal: e.target.value }))}
                    />
                  </label>
                  <label>
                    Đến (+07:00)
                    <input
                      type="datetime-local"
                      aria-label="Mốc Đến (+07:00)"
                      value={draft.toLocal}
                      onChange={e => setDraft(prev => ({ ...prev, toLocal: e.target.value }))}
                    />
                  </label>
                </>
              )}

              <div className="row" style={{ alignItems: 'flex-end' }}>
                <button type="button" disabled={storeState.loading} onClick={handleApplyFilters}>
                  Áp dụng bộ lọc
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={storeState.loading}
                  onClick={handleResetFilters}
                >
                  Xóa lọc
                </button>
              </div>
            </FilterBar>

            <DataTable
              columns={columns}
              items={storeState.items}
              getKey={item => item.id}
              loading={storeState.loading}
              loaded={storeState.loaded}
              emptyTitle={
                appliedDraft.view === 'active'
                  ? 'Không có cảnh báo đang hiệu lực'
                  : 'Không có episode nào trong khoảng lịch sử này'
              }
              emptyDescription={
                evalSummary.pending > 0 || evalSummary.error > 0
                  ? 'Lưu ý: một số Node trên Farm đang ở trạng thái PENDING hoặc ERROR đối soát; kết quả hiện có chỉ phản ánh dữ liệu đã hoàn tất evaluation.'
                  : 'Thử nới lỏng bộ lọc hoặc chuyển sang tab Lịch sử để rà soát các episode đã đóng.'
              }
            />

            <CursorPager
              count={storeState.items.length}
              nextCursor={storeState.nextCursor}
              busy={storeState.loadingMore}
              onLoadMore={() => void storeRef.current?.loadMore()}
            />
          </div>
        </>
      )}
    </div>
  )
}
