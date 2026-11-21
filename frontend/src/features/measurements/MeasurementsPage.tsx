import { Suspense, lazy, useEffect, useState, useSyncExternalStore } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useOptionalOperationalFarm } from '../../app/OperationalFarmContext'
import { CursorPager } from '../../components/CursorPager'
import { DataTable } from '../../components/DataTable'
import type { Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { FilterBar } from '../../components/FilterBar'
import { MetricCard } from '../../components/MetricCard'
import { PageHeader } from '../../components/PageHeader'
import { StateBanner } from '../../components/StateBanner'
import { StatusBadge } from '../../components/StatusBadge'
import { useAuth } from '../auth/AuthProvider'
import type { ChartPageSize } from './chartData'
import {
  METRIC_META,
  areaLifecycleBadge,
  formatEpochToVi,
  formatUtcToVi,
  receivingBadge,
  sensorStatusLabel,
} from './format'
import { FarmNodesLoader, describeApiError, isAbortOrStale } from './scopeLoader'
import { MeasurementsStore } from './store'
import type { HistoryViewTab, TimeMode } from './store'
import type { MeasurementView, NodeChoice } from './types'

const MeasurementsChartView = lazy(() => import('./MeasurementsChartView'))

export function MeasurementsPage() {
  const { session } = useAuth()
  return session ? <MeasurementsViewPanel key={session.csrf_token} /> : null
}

function MeasurementsViewPanel() {
  const farmCtx = useOptionalOperationalFarm()
  const selectedFarm = farmCtx?.selectedFarm ?? null
  const [searchParams, setSearchParams] = useSearchParams()

  const [store] = useState(() => new MeasurementsStore())
  const snap = useSyncExternalStore(store.subscribe, store.getSnapshot)

  const [nodesLoader] = useState(() => new FarmNodesLoader())
  const [nodes, setNodes] = useState<NodeChoice[]>([])
  const [zones, setZones] = useState<Array<{ id: string; code: string; name: string; archived_at: string | null }>>([])
  const [zoneFilter, setZoneFilter] = useState('')
  const [scopeLoading, setScopeLoading] = useState(false)
  const [scopeError, setScopeError] = useState('')

  useEffect(() => {
    return () => {
      nodesLoader.dispose()
      store.dispose()
    }
  }, [nodesLoader, store])

  // Load zones + nodes whenever operational Farm changes
  useEffect(() => {
    let active = true
    setZoneFilter('')
    setNodes([])
    setZones([])
    setScopeError('')
    store.setNodeId('')

    if (!selectedFarm) return

    const paramNodeId = searchParams.get('node_id') ?? ''
    const paramMode: TimeMode = searchParams.get('mode') === 'unknown' ? 'unknown' : 'known'
    const paramTab: HistoryViewTab =
      paramMode === 'known' && searchParams.get('tab') === 'chart' ? 'chart' : 'table'

    setScopeLoading(true)
    void nodesLoader
      .load(selectedFarm.id)
      .then(result => {
        if (!active || result === null) return
        setZones(result.zones)
        setNodes(result.nodes)
        setScopeLoading(false)
        if (paramNodeId && result.nodes.some(n => n.id === paramNodeId)) {
          store.setNodeId(paramNodeId, { timeMode: paramMode, viewTab: paramTab })
          void store.loadCurrent()
          if (paramMode === 'known' && paramTab === 'chart') {
            void store.loadChart(false)
          } else {
            void store.loadHistory(false)
          }
        } else if (paramMode === 'unknown') {
          store.setTimeMode('unknown')
        } else if (paramTab === 'chart') {
          store.setViewTab('chart')
        }
      })
      .catch(error => {
        if (!active || isAbortOrStale(error)) return
        setScopeLoading(false)
        setScopeError(describeApiError(error, 'Không tải được danh sách Zone/Node của Farm.'))
      })

    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFarm?.id, farmCtx?.contextGeneration])

  const filteredNodes = zoneFilter ? nodes.filter(n => n.zone.id === zoneFilter) : nodes
  const selectedNode = nodes.find(n => n.id === snap.nodeId) ?? null

  function selectNode(nextNodeId: string, nextMode?: TimeMode) {
    const mode = nextMode ?? snap.history.timeMode
    const activeTab: HistoryViewTab = mode === 'unknown' ? 'table' : snap.history.viewTab
    store.setNodeId(nextNodeId, { timeMode: mode, viewTab: activeTab })
    const nextParams = new URLSearchParams(searchParams)
    if (nextNodeId) nextParams.set('node_id', nextNodeId)
    else nextParams.delete('node_id')
    if (mode === 'unknown') {
      nextParams.set('mode', 'unknown')
      nextParams.delete('tab')
    } else {
      nextParams.delete('mode')
      if (activeTab === 'chart') nextParams.set('tab', 'chart')
      else nextParams.delete('tab')
    }
    setSearchParams(nextParams, { replace: true })
    if (nextNodeId) {
      void store.loadCurrent()
      if (mode === 'known' && activeTab === 'chart') {
        void store.loadChart(false)
      } else {
        void store.loadHistory(false)
      }
    }
  }

  function switchMode(mode: TimeMode) {
    store.setTimeMode(mode)
    const nextParams = new URLSearchParams(searchParams)
    if (mode === 'unknown') {
      nextParams.set('mode', 'unknown')
      nextParams.delete('tab')
    } else {
      nextParams.delete('mode')
      if (store.getSnapshot().history.viewTab === 'chart') nextParams.set('tab', 'chart')
      else nextParams.delete('tab')
    }
    setSearchParams(nextParams, { replace: true })
    if (snap.nodeId) {
      if (mode === 'known' && store.getSnapshot().history.viewTab === 'chart') {
        void store.loadChart(false)
      } else {
        void store.loadHistory(false)
      }
    }
  }

  function switchViewTab(tab: HistoryViewTab) {
    if (snap.history.timeMode === 'unknown' && tab === 'chart') return
    store.setViewTab(tab)
    const nextParams = new URLSearchParams(searchParams)
    if (tab === 'chart') nextParams.set('tab', 'chart')
    else nextParams.delete('tab')
    setSearchParams(nextParams, { replace: true })
    if (!snap.nodeId || snap.history.timeMode !== 'known') return

    const latest = store.getSnapshot()
    if (tab === 'chart') {
      if (latest.chart.state === null || latest.chart.viewToken !== latest.history.viewToken) {
        void store.loadChart(false)
      }
    } else if (latest.history.state === null) {
      void store.loadHistory(false, { reuseViewToken: Boolean(latest.history.viewToken) })
    }
  }

  function applyKnownFilter() {
    if (snap.history.viewTab === 'chart') {
      void store.loadChart(false, { freshSnapshot: true })
    } else {
      void store.loadHistory(false)
    }
  }

  function resetToDefault24h() {
    store.resetDefaultFilter()
    if (snap.history.viewTab === 'chart') {
      void store.loadChart(false, { freshSnapshot: true })
    } else {
      void store.loadHistory(false)
    }
  }

  function handleChangeChartPageSize(pageSize: ChartPageSize) {
    store.setChartPageSize(pageSize)
    if (snap.nodeId && snap.history.timeMode === 'known') {
      void store.loadChart(false)
    }
  }

  const historyColumns: Column<MeasurementView>[] = [
    ...(snap.history.timeMode === 'known'
      ? [
          {
            key: 'event_time',
            header: 'Giờ đo (UTC+07:00)',
            render: (item: MeasurementView) => (
              <span className="tabular">{formatUtcToVi(item.event_time)}</span>
            ),
          },
        ]
      : [
          {
            key: 'time_status',
            header: 'Trạng thái giờ đo',
            render: () => <StatusBadge tone="neutral" label="Chưa xác định (UNKNOWN)" />,
          },
        ]),
    {
      key: 'identity',
      header: 'Boot / Seq',
      render: item => (
        <span className="mono tabular">
          {item.identity.boot_id} / {item.identity.seq}
        </span>
      ),
    },
    ...METRIC_META.map(meta => ({
      key: meta.code,
      header: `${meta.label} (${meta.unit})`,
      render: (item: MeasurementView) => {
        const m = item.measurements[meta.code]
        if (m.value !== null && m.status === 'OK') {
          return <span className="mono tabular">{m.value}</span>
        }
        return <StatusBadge tone="err" label={sensorStatusLabel(m.status)} />
      },
    })),
    {
      key: 'gateway_received',
      header: 'Gateway nhận (+07)',
      render: item => <span className="tabular">{formatEpochToVi(item.gateway_received_at_epoch)}</span>,
    },
    {
      key: 'persisted_at',
      header: 'Backend lưu (+07)',
      render: item => <span className="tabular">{formatUtcToVi(item.persisted_at)}</span>,
    },
  ]

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Vận hành · G3 · FL-07"
        title="Dữ liệu đo"
        description="Tra cứu số đo hiện tại, lịch sử có giờ đo (Known), bản ghi chưa xác định giờ đo (Unknown) và biểu đồ chuỗi thời gian."
      />

      {!selectedFarm && (
        <StateBanner
          tone="warn"
          message="Chưa chọn Farm vận hành. Vui lòng chọn tường minh một Farm từ bộ chọn trên Topbar (bao gồm Farm đã lưu trữ khi cần tra cứu lịch sử)."
        />
      )}

      {selectedFarm && (
        <section className="card stack-sm" aria-label="Phạm vi khu vực và thiết bị">
          <div className="row-between">
            <div className="row">
              <strong>{selectedFarm.code}</strong>
              <span>{selectedFarm.name}</span>
              <StatusBadge
                tone={areaLifecycleBadge(selectedFarm.archived_at).tone}
                label={areaLifecycleBadge(selectedFarm.archived_at).label}
              />
            </div>
            {selectedNode && (
              <div className="row">
                <StatusBadge
                  tone={areaLifecycleBadge(selectedNode.zone.archived_at).tone}
                  label={`Zone ${selectedNode.zone.code}: ${areaLifecycleBadge(selectedNode.zone.archived_at).label}`}
                />
                <StatusBadge
                  tone={receivingBadge(selectedNode.receiving_enabled).tone}
                  label={receivingBadge(selectedNode.receiving_enabled).label}
                />
              </div>
            )}
          </div>

          {scopeError && <StateBanner tone="err" message={scopeError} />}

          <FilterBar>
            <div className="filter-field">
              <label htmlFor="meas-zone-filter">Lọc Zone (tùy chọn)</label>
              <select
                id="meas-zone-filter"
                value={zoneFilter}
                disabled={scopeLoading}
                onChange={event => {
                  const nextZone = event.target.value
                  setZoneFilter(nextZone)
                  if (
                    snap.nodeId &&
                    nextZone &&
                    nodes.find(n => n.id === snap.nodeId)?.zone.id !== nextZone
                  ) {
                    selectNode('')
                  }
                }}
              >
                <option value="">Tất cả Zone trong Farm</option>
                {zones.map(z => (
                  <option key={z.id} value={z.id}>
                    {z.archived_at ? `[Đã lưu trữ] ${z.code} · ${z.name}` : `${z.code} · ${z.name}`}
                  </option>
                ))}
              </select>
            </div>

            <div className="filter-field">
              <label htmlFor="meas-node-select">Chọn Node *</label>
              <select
                id="meas-node-select"
                value={snap.nodeId}
                disabled={scopeLoading}
                onChange={event => selectNode(event.target.value)}
              >
                <option value="">-- Chọn Node để xem dữ liệu --</option>
                {filteredNodes.map(n => (
                  <option key={n.id} value={n.id}>
                    {n.device_id} · {n.name} ({n.zone.code})
                  </option>
                ))}
              </select>
            </div>
          </FilterBar>
        </section>
      )}

      {selectedFarm && snap.nodeId && (
        <>
          {/* Block 1: Current measurement (independent from history range filter) */}
          <section className="card stack" aria-label="Dữ liệu hiện tại">
            <div className="card-header">
              <div>
                <h2>Số đo hiện tại</h2>
                <small className="muted">
                  Tách biệt hoàn toàn khỏi bộ lọc khoảng thời gian của lịch sử bên dưới.
                </small>
              </div>
              <button
                type="button"
                className="btn-secondary btn-sm"
                disabled={snap.current.loading}
                onClick={() => void store.loadCurrent()}
              >
                {snap.current.loading ? 'Đang tải…' : 'Làm mới hiện tại'}
              </button>
            </div>

            {snap.current.error && (
              <StateBanner
                tone="err"
                message={
                  snap.current.showingPrevious
                    ? `Không tải được bản hiện tại mới: ${snap.current.error} (đang giữ lần tải trước)`
                    : `Không tải được dữ liệu: ${snap.current.error}`
                }
              />
            )}

            {snap.current.loading && !snap.current.data && (
              <p className="muted" role="status">
                Đang tải bản ghi hiện tại…
              </p>
            )}

            {snap.current.data && snap.current.data.state === 'NO_DATA' && (
              <EmptyState
                title="Chưa có dữ liệu"
                description={`Node này chưa có bản ghi nào tính đến ${formatUtcToVi(snap.current.data.as_of)}.`}
              />
            )}

            {snap.current.data && snap.current.data.state === 'NO_EVENT_TIME' && (
              <EmptyState
                title="Chưa có dữ liệu xác định giờ đo"
                description="Backend chỉ có bản ghi chưa xác định giờ đo (UNKNOWN) và không dùng giờ nhận làm giờ đo."
                action={
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => switchMode('unknown')}
                  >
                    Xem nhóm Chưa xác định giờ đo (UNKNOWN)
                  </button>
                }
              />
            )}

            {snap.current.data &&
              snap.current.data.state === 'AVAILABLE' &&
              snap.current.data.measurement && (
                <div className="stack">
                  <div className="row-between">
                    <span className="tabular">
                      <strong>Giờ đo:</strong>{' '}
                      {formatUtcToVi(snap.current.data.measurement.event_time)}
                    </span>
                    <small className="tabular muted">
                      Chốt lúc: {formatUtcToVi(snap.current.data.as_of)} · Boot{' '}
                      {snap.current.data.measurement.identity.boot_id} / Seq{' '}
                      {snap.current.data.measurement.identity.seq}
                    </small>
                  </div>
                  <div className="metric-grid">
                    {METRIC_META.map(meta => (
                      <MetricCard
                        key={meta.code}
                        label={meta.label}
                        metric={snap.current.data?.measurement?.measurements[meta.code] ?? null}
                        fallbackUnit={meta.unit}
                      />
                    ))}
                  </div>
                </div>
              )}
          </section>

          {/* Block 2: History (Known / Unknown) & Chart notice */}
          <section className="card stack" aria-label="Lịch sử và biểu đồ dữ liệu đo">
            <div className="card-header">
              <h2>Lịch sử &amp; Biểu đồ chuỗi thời gian</h2>
              <div className="row">
                <button
                  type="button"
                  className={snap.history.timeMode === 'known' ? 'tab-btn active' : 'tab-btn'}
                  onClick={() => switchMode('known')}
                >
                  Có giờ đo (Known)
                </button>
                <button
                  type="button"
                  className={snap.history.timeMode === 'unknown' ? 'tab-btn active' : 'tab-btn'}
                  onClick={() => switchMode('unknown')}
                >
                  Chưa xác định giờ đo (Unknown)
                </button>
              </div>
            </div>

            {snap.history.timeMode === 'known' ? (
              <>
                <FilterBar>
                  <div className="filter-field">
                    <label htmlFor="meas-from-input">Từ (UTC+07:00)</label>
                    <input
                      id="meas-from-input"
                      type="datetime-local"
                      value={snap.history.fromInput}
                      onChange={event =>
                        store.setFilterInputs(event.target.value, snap.history.toInput)
                      }
                    />
                  </div>
                  <div className="filter-field">
                    <label htmlFor="meas-to-input">Đến (UTC+07:00)</label>
                    <input
                      id="meas-to-input"
                      type="datetime-local"
                      value={snap.history.toInput}
                      onChange={event =>
                        store.setFilterInputs(snap.history.fromInput, event.target.value)
                      }
                    />
                  </div>
                  <div className="filter-actions">
                    <button
                      type="button"
                      disabled={
                        snap.history.loading || snap.chart.loading || snap.chart.loadingBatch
                      }
                      onClick={applyKnownFilter}
                    >
                      {snap.history.loading || snap.chart.loading ? 'Đang lọc…' : 'Lọc dữ liệu'}
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={
                        snap.history.loading || snap.chart.loading || snap.chart.loadingBatch
                      }
                      onClick={resetToDefault24h}
                    >
                      Mặc định 24h
                    </button>
                  </div>
                </FilterBar>

                <div className="tabs" role="tablist" aria-label="Chế độ xem lịch sử">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={snap.history.viewTab === 'table'}
                    className={`tab-btn${snap.history.viewTab === 'table' ? ' active' : ''}`}
                    onClick={() => switchViewTab('table')}
                  >
                    Bảng lịch sử
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={snap.history.viewTab === 'chart'}
                    className={`tab-btn${snap.history.viewTab === 'chart' ? ' active' : ''}`}
                    onClick={() => switchViewTab('chart')}
                  >
                    Biểu đồ chuỗi thời gian
                  </button>
                </div>
              </>
            ) : (
              <StateBanner
                tone="info"
                message="Chế độ Chưa xác định giờ đo (UNKNOWN) sắp theo thời điểm Backend lưu mới nhất và không áp dụng bộ lọc giờ đo (from/to)."
                action={
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    disabled={snap.history.loading}
                    onClick={() => void store.loadHistory(false)}
                  >
                    Làm mới danh sách UNKNOWN
                  </button>
                }
              />
            )}

            {snap.history.error &&
              (snap.history.timeMode === 'unknown' || snap.history.viewTab === 'table') && (
                <StateBanner
                  tone="err"
                  message={
                    snap.history.showingPrevious
                      ? `Không tải được dữ liệu: ${snap.history.error} (đang hiển thị dữ liệu lần tải trước)`
                      : `Không tải được dữ liệu: ${snap.history.error}`
                  }
                />
              )}

            {snap.history.asOf && (
              <div className="row-between">
                <small className="tabular muted">
                  Snapshot chốt lúc: {formatUtcToVi(snap.history.asOf)}
                  {snap.history.range
                    ? ` · Khoảng: ${formatUtcToVi(snap.history.range.from)} → ${formatUtcToVi(snap.history.range.to)}`
                    : ''}
                </small>
                {snap.history.viewToken && (
                  <small className="mono muted">Đã gắn view_token snapshot dùng chung</small>
                )}
              </div>
            )}

            {snap.history.viewTab === 'chart' && snap.history.timeMode === 'known' ? (
              <Suspense
                fallback={
                  <p className="muted" role="status">
                    Đang tải thành phần biểu đồ…
                  </p>
                }
              >
                <MeasurementsChartView
                  chart={snap.chart}
                  range={snap.history.range}
                  onLoadMore={() => void store.loadChart(true)}
                  onLoadBatch={() => void store.loadChartBatch()}
                  onCancelBatch={() => store.cancelChartBatch()}
                  onReloadFresh={() => void store.loadChart(false, { freshSnapshot: true })}
                  onChangePageSize={handleChangeChartPageSize}
                  onSwitchUnknown={() => switchMode('unknown')}
                />
              </Suspense>
            ) : snap.history.loading && snap.history.state === null ? (
              <p className="muted" role="status">
                Đang tải lịch sử dữ liệu đo…
              </p>
            ) : snap.history.state === 'NO_DATA' ? (
              <EmptyState
                title="Chưa có dữ liệu"
                description="Node chưa có bản ghi nào trong cơ sở dữ liệu."
              />
            ) : snap.history.state === 'NO_EVENT_TIME' ? (
              <EmptyState
                title="Chưa có dữ liệu xác định giờ đo"
                description="Tất cả bản ghi hiện có đều thiếu giờ đo (UNKNOWN)."
                action={
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => switchMode('unknown')}
                  >
                    Mở nhóm Chưa xác định giờ đo
                  </button>
                }
              />
            ) : snap.history.state === 'EMPTY_RANGE' ? (
              <EmptyState
                title={
                  snap.history.timeMode === 'unknown'
                    ? 'Không có bản ghi thiếu giờ đo (UNKNOWN)'
                    : 'Không có bản ghi trong khoảng lọc'
                }
                description={
                  snap.history.timeMode === 'unknown'
                    ? 'Node này đã có bản ghi xác định giờ đo nhưng không có bản ghi UNKNOWN.'
                    : 'Hãy mở rộng khoảng Từ/Đến (UTC+07:00) hoặc kiểm tra nhóm UNKNOWN.'
                }
              />
            ) : (
              <>
                <DataTable
                  columns={historyColumns}
                  items={snap.history.items}
                  getKey={item => item.id}
                  loading={snap.history.loading}
                  loaded={snap.history.state !== null}
                  emptyTitle="Không có dữ liệu"
                />
                <CursorPager
                  count={snap.history.items.length}
                  nextCursor={snap.history.nextCursor}
                  busy={snap.history.loadingMore}
                  onLoadMore={() => void store.loadHistory(true)}
                />
              </>
            )}
          </section>
        </>
      )}
    </div>
  )
}
