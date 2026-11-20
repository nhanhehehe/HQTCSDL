import { useEffect, useState, useSyncExternalStore } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useOptionalOperationalFarm } from '../../app/OperationalFarmContext'
import { DataTable } from '../../components/DataTable'
import type { Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { FilterBar } from '../../components/FilterBar'
import { PageHeader } from '../../components/PageHeader'
import { StateBanner } from '../../components/StateBanner'
import { StatusBadge } from '../../components/StatusBadge'
import { useAuth } from '../auth/AuthProvider'
import { METRIC_META, areaLifecycleBadge, formatUtcToVi } from '../measurements/format'
import { FarmNodesLoader, describeApiError, isAbortOrStale } from '../measurements/scopeLoader'
import type { NodeChoice } from '../measurements/types'
import { ReportsStore } from './store'
import type { MetricStatsView } from './types'

const METRIC_LABELS = new Map(METRIC_META.map(m => [m.code, m.label]))

function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.style.display = 'none'
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
  } finally {
    URL.revokeObjectURL(url)
  }
}

export function ReportsPage() {
  const { session } = useAuth()
  return session ? <ReportsViewPanel key={session.csrf_token} csrf={session.csrf_token} /> : null
}

function ReportsViewPanel({ csrf }: { csrf: string }) {
  const { canWrite } = useAuth()
  const farmCtx = useOptionalOperationalFarm()
  const selectedFarm = farmCtx?.selectedFarm ?? null
  const [searchParams] = useSearchParams()

  const [store] = useState(() => new ReportsStore())
  const snap = useSyncExternalStore(store.subscribe, store.getSnapshot)

  const [nodesLoader] = useState(() => new FarmNodesLoader())
  const [nodes, setNodes] = useState<NodeChoice[]>([])
  const [zones, setZones] = useState<
    Array<{ id: string; code: string; name: string; archived_at: string | null }>
  >([])
  const [zoneFilter, setZoneFilter] = useState('')
  const [scopeLoading, setScopeLoading] = useState(false)
  const [scopeError, setScopeError] = useState('')

  useEffect(() => {
    return () => {
      nodesLoader.dispose()
      store.dispose()
    }
  }, [nodesLoader, store])

  useEffect(() => {
    let active = true
    setZoneFilter('')
    setNodes([])
    setZones([])
    setScopeError('')
    store.resetForFarmChange(farmCtx?.contextGeneration ?? 0)

    if (!selectedFarm) return

    const paramNodeId = searchParams.get('node_id') ?? ''
    setScopeLoading(true)
    void nodesLoader
      .load(selectedFarm.id)
      .then(result => {
        if (!active || result === null) return
        setZones(result.zones)
        setNodes(result.nodes)
        setScopeLoading(false)
        if (paramNodeId && result.nodes.some(n => n.id === paramNodeId)) {
          // Pre-fill Draft only (FL-12/FL-13: never auto-create report on navigation).
          store.setNodeId(paramNodeId)
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
  const canExport = store.canExportCsv(canWrite())

  const columns: Column<MetricStatsView>[] = [
    {
      key: 'metric',
      header: 'Đại lượng',
      render: row => (
        <div>
          <strong>{METRIC_LABELS.get(row.metric_code) ?? row.metric_code}</strong>{' '}
          <small className="mono muted">({row.metric_code})</small>
        </div>
      ),
    },
    {
      key: 'unit',
      header: 'Đơn vị',
      render: row => <span>{row.unit}</span>,
    },
    {
      key: 'n_valid',
      header: 'Hợp lệ',
      render: row => <span className="mono tabular">{row.n_valid}</span>,
    },
    {
      key: 'n_invalid',
      header: 'Lỗi',
      render: row => <span className="mono tabular">{row.n_invalid}</span>,
    },
    {
      key: 'min',
      header: 'Nhỏ nhất',
      render: row => <span className="mono tabular">{row.min ?? '—'}</span>,
    },
    {
      key: 'avg',
      header: 'Trung bình (Half-Up)',
      render: row => <strong className="mono tabular">{row.avg ?? '—'}</strong>,
    },
    {
      key: 'max',
      header: 'Lớn nhất',
      render: row => <span className="mono tabular">{row.max ?? '—'}</span>,
    },
  ]

  async function handleExportCsv() {
    const csv = await store.exportCsv(csrf, canWrite())
    if (csv) {
      triggerBlobDownload(csv.blob, csv.filename)
    }
  }

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Vận hành · G3 · FL-13"
        title="Báo cáo thống kê & CSV"
        description="Tổng hợp thống kê 5 đại lượng chuẩn (half-up tối đa 2 chữ số) và xuất tệp CSV khớp đúng snapshot đang xem."
      />

      {!selectedFarm && (
        <StateBanner
          tone="warn"
          message="Chưa chọn Farm vận hành. Vui lòng chọn tường minh một Farm từ bộ chọn trên Topbar."
        />
      )}

      {selectedFarm && (
        <section className="card stack" aria-label="Bộ lọc tạo báo cáo">
          <div className="row-between">
            <div className="row">
              <strong>Bộ lọc tạo báo cáo (Draft)</strong>
              <StatusBadge
                tone={areaLifecycleBadge(selectedFarm.archived_at).tone}
                label={`${selectedFarm.code}: ${areaLifecycleBadge(selectedFarm.archived_at).label}`}
              />
            </div>
            <small className="muted">Múi giờ lọc: UTC+07:00</small>
          </div>

          {scopeError && <StateBanner tone="err" message={scopeError} />}
          {snap.error && <StateBanner tone="err" message={snap.error} />}

          <FilterBar>
            <div className="filter-field">
              <label htmlFor="rep-zone-filter">Lọc Zone (tùy chọn)</label>
              <select
                id="rep-zone-filter"
                value={zoneFilter}
                disabled={scopeLoading}
                onChange={event => {
                  const nextZone = event.target.value
                  setZoneFilter(nextZone)
                  if (
                    snap.draft.nodeId &&
                    nextZone &&
                    nodes.find(n => n.id === snap.draft.nodeId)?.zone.id !== nextZone
                  ) {
                    store.setNodeId('')
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
              <label htmlFor="rep-node-select">Node *</label>
              <select
                id="rep-node-select"
                value={snap.draft.nodeId}
                disabled={scopeLoading}
                onChange={event => store.setNodeId(event.target.value)}
              >
                <option value="">-- Chọn Node để tạo báo cáo --</option>
                {filteredNodes.map(n => (
                  <option key={n.id} value={n.id}>
                    {n.device_id} · {n.name} ({n.zone.code})
                  </option>
                ))}
              </select>
            </div>

            <div className="filter-field">
              <label htmlFor="rep-from-input">Từ (UTC+07:00)</label>
              <input
                id="rep-from-input"
                type="datetime-local"
                value={snap.draft.fromInput}
                onChange={event => store.setFromInput(event.target.value)}
              />
            </div>

            <div className="filter-field">
              <label htmlFor="rep-to-input">Đến (UTC+07:00)</label>
              <input
                id="rep-to-input"
                type="datetime-local"
                value={snap.draft.toInput}
                onChange={event => store.setToInput(event.target.value)}
              />
            </div>

            <div className="filter-actions">
              <button
                type="button"
                disabled={snap.creating || !canWrite()}
                onClick={() => void store.createReport(csrf, canWrite())}
              >
                {snap.creating ? 'Đang tạo báo cáo…' : 'Tạo báo cáo'}
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={snap.creating}
                onClick={() => store.applyDefault24h()}
              >
                Mặc định 24h
              </button>
            </div>
          </FilterBar>
        </section>
      )}

      {selectedFarm && (
        <section className="card stack" aria-label="Kết quả báo cáo snapshot">
          <div className="card-header">
            <div>
              <h2>Kết quả báo cáo (Snapshot)</h2>
              <small className="muted">
                Xuất CSV sử dụng trực tiếp snapshot đã ký (`REPORT_V1`), không truy vấn lại bảng
                đo.
              </small>
            </div>
            <button
              type="button"
              disabled={!canExport}
              onClick={() => void handleExportCsv()}
            >
              {snap.exporting ? 'Đang xuất CSV…' : 'Xuất CSV (Khớp snapshot)'}
            </button>
          </div>

          {snap.result && !snap.resultMatchesDraft && (
            <StateBanner
              tone="warn"
              message="Kết quả của bộ lọc cũ — bộ lọc Draft đã thay đổi, cần bấm Tạo báo cáo lại trước khi xuất CSV."
            />
          )}

          {snap.exportError && <StateBanner tone="err" message={snap.exportError} />}
          {snap.exportSuccess && <StateBanner tone="ok" message={snap.exportSuccess} />}

          {!snap.result ? (
            <EmptyState
              title="Chưa tạo báo cáo"
              description="Chọn Node và khoảng thời gian (hoặc để trống để lấy 24 giờ gần nhất theo giờ Backend) rồi bấm Tạo báo cáo."
            />
          ) : (
            <>
              <dl className="kv-grid">
                <div className="kv-item">
                  <dt>Nguồn &amp; Phạm vi</dt>
                  <dd>
                    <span className="mono">{snap.result.context.source}</span> ·{' '}
                    {snap.result.context.farm.code} / {snap.result.context.zone.code} /{' '}
                    <strong>{snap.result.context.node.device_id}</strong>
                  </dd>
                </div>
                <div className="kv-item">
                  <dt>Khoảng giờ đo ({snap.result.context.timezone})</dt>
                  <dd className="tabular">
                    {formatUtcToVi(snap.result.context.from)} →{' '}
                    {formatUtcToVi(snap.result.context.to)}
                  </dd>
                </div>
                <div className="kv-item">
                  <dt>Chốt lúc / Tổng bản ghi (N_total)</dt>
                  <dd className="tabular">
                    {formatUtcToVi(snap.result.as_of)} ·{' '}
                    <strong className="mono">{snap.result.N_total}</strong> bản ghi
                  </dd>
                </div>
                <div className="kv-item">
                  <dt>Hiệu lực xuất CSV đến</dt>
                  <dd className="tabular">{formatUtcToVi(snap.result.expires_at)}</dd>
                </div>
              </dl>

              <DataTable
                columns={columns}
                items={snap.result.rows}
                getKey={row => row.metric_code}
              />

              <div className="row-between">
                <small className="muted">
                  Lưu ý: Các bản ghi chưa xác định giờ đo (UNKNOWN) không được tính vào báo cáo
                  thống kê này.
                </small>
                <Link
                  className="btn btn-secondary btn-sm"
                  to={`/measurements?node_id=${encodeURIComponent(snap.result.context.node.id)}&mode=unknown`}
                >
                  Xem nhóm UNKNOWN tại Dữ liệu đo (FL-07)
                </Link>
              </div>
            </>
          )}
        </section>
      )}
    </div>
  )
}
