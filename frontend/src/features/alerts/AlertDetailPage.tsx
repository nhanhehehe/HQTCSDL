import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  getAlertDetail,
  postAlertAcknowledgement,
} from '../../api/alerts'
import type {
  AlertAcknowledgementView,
  AlertDetailResponse,
  AlertReplacementView,
  AlertRevisionView,
  AuditKind,
} from '../../api/alerts'
import { ApiError } from '../../api/client'
import { useOptionalOperationalFarm } from '../../app/OperationalFarmContext'
import { CursorPager } from '../../components/CursorPager'
import { DataTable } from '../../components/DataTable'
import type { Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { PageHeader } from '../../components/PageHeader'
import { StateBanner } from '../../components/StateBanner'
import type { BannerTone } from '../../components/StateBanner'
import { StatusBadge } from '../../components/StatusBadge'
import { useAuth } from '../auth/AuthProvider'
import { formatUtcToVi } from '../measurements/format'
import { describeApiError, isAbortOrStale } from '../measurements/scopeLoader'
import {
  formatAlertTitle,
  formatEndReason,
  formatReplacementReason,
} from './store'

export function AlertDetailPage() {
  const { session } = useAuth()
  const { id } = useParams<{ id: string }>()
  if (!session || !id) return null
  return <AlertDetailInner key={`${session.csrf_token}:${id}`} csrf={session.csrf_token} episodeId={id} />
}

function evaluationBadge(status: 'UP_TO_DATE' | 'PENDING' | 'ERROR') {
  if (status === 'UP_TO_DATE') return <StatusBadge tone="ok" label="UP_TO_DATE" />
  if (status === 'PENDING') return <StatusBadge tone="warn" label="PENDING" />
  return <StatusBadge tone="err" label="ERROR" />
}

function AlertDetailInner({ csrf, episodeId }: { csrf: string; episodeId: string }) {
  const { canWrite } = useAuth()
  const farmCtx = useOptionalOperationalFarm()
  const farms = farmCtx?.farms ?? []
  const selectedFarmId = farmCtx?.selectedFarmId ?? ''
  const selectedFarm = farmCtx?.selectedFarm ?? null
  const selectFarm = farmCtx?.selectFarm

  const [detail, setDetail] = useState<AlertDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  // Ack form state
  const [note, setNote] = useState('')
  const [ackBusy, setAckBusy] = useState(false)
  const [ackMsg, setAckMsg] = useState('')
  const [ackTone, setAckTone] = useState<BannerTone>('info')
  const [ackUncertainVersion, setAckUncertainVersion] = useState<number | null>(null)
  const [ackLookupResult, setAckLookupResult] = useState<AlertAcknowledgementView | null>(null)

  // Lazy audit tab state
  const [activeAuditTab, setActiveAuditTab] = useState<AuditKind | null>(null)
  const [auditRevisions, setAuditRevisions] = useState<AlertRevisionView[]>([])
  const [auditAcks, setAuditAcks] = useState<AlertAcknowledgementView[]>([])
  const [auditReplacements, setAuditReplacements] = useState<AlertReplacementView[]>([])
  const [auditCursor, setAuditCursor] = useState<string | null>(null)
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditLoadedKind, setAuditLoadedKind] = useState<AuditKind | null>(null)
  const [auditError, setAuditError] = useState('')

  const detailController = useRef<AbortController | null>(null)
  const auditController = useRef<AbortController | null>(null)
  const detailGen = useRef(0)
  const auditGen = useRef(0)
  const operation = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      detailGen.current += 1
      auditGen.current += 1
      detailController.current?.abort()
      auditController.current?.abort()
    }
  }, [])

  const loadDetail = useCallback(async () => {
    detailGen.current += 1
    const gen = detailGen.current
    detailController.current?.abort()
    const controller = new AbortController()
    detailController.current = controller
    setLoading(true)
    setLoadError('')
    try {
      const res = await getAlertDetail(episodeId, {}, controller.signal)
      if (!mounted.current || gen !== detailGen.current || controller.signal.aborted) return
      setDetail(res)
      setLoading(false)
    } catch (error) {
      if (!mounted.current || gen !== detailGen.current || isAbortOrStale(error)) return
      setLoading(false)
      setLoadError(describeApiError(error, 'Không tải được chi tiết episode cảnh báo.'))
    }
  }, [episodeId])

  useEffect(() => {
    void loadDetail()
  }, [loadDetail])

  const loadAuditTab = useCallback(
    async (kind: AuditKind, cursor?: string) => {
      auditGen.current += 1
      const gen = auditGen.current
      auditController.current?.abort()
      const controller = new AbortController()
      auditController.current = controller

      setActiveAuditTab(kind)
      setAuditLoading(true)
      setAuditError('')
      if (!cursor) {
        setAuditCursor(null)
        setAuditLoadedKind(null)
      }

      try {
        const res = await getAlertDetail(
          episodeId,
          {
            audit_kind: kind,
            audit_page_size: 15,
            ...(cursor ? { audit_cursor: cursor } : {}),
          },
          controller.signal,
        )
        if (!mounted.current || gen !== auditGen.current || controller.signal.aborted) return
        setDetail(res)
        const audit = res.audit
        if (audit && audit.kind === 'revisions') {
          setAuditRevisions(prev => (cursor ? [...prev, ...audit.items] : audit.items))
          setAuditCursor(audit.next_cursor)
        } else if (audit && audit.kind === 'acknowledgements') {
          setAuditAcks(prev => (cursor ? [...prev, ...audit.items] : audit.items))
          setAuditCursor(audit.next_cursor)
        } else if (audit && audit.kind === 'replacements') {
          setAuditReplacements(prev => (cursor ? [...prev, ...audit.items] : audit.items))
          setAuditCursor(audit.next_cursor)
        }
        setAuditLoadedKind(kind)
        setAuditLoading(false)
      } catch (error) {
        if (!mounted.current || gen !== auditGen.current || isAbortOrStale(error)) return
        setAuditLoading(false)
        setAuditError(describeApiError(error, 'Không tải được dữ liệu audit.'))
      }
    },
    [episodeId],
  )

  async function submitAcknowledgement() {
    if (!detail || operation.current || !canWrite()) return
    const episode = detail.episode
    if (!episode.is_current) {
      setAckTone('warn')
      setAckMsg('Episode đã được thay thế (retired), không cho phép xác nhận.')
      return
    }
    const trimmedNote = note.trim()
    if (trimmedNote.length > 1000) {
      setAckTone('err')
      setAckMsg('Ghi chú xác nhận không được vượt quá 1000 ký tự.')
      return
    }

    operation.current = true
    setAckBusy(true)
    setAckMsg('')
    setAckLookupResult(null)
    const sentAckVersion = episode.acknowledgement_version
    const sentViewedRev = episode.current_revision.revision_no

    try {
      const res = await postAlertAcknowledgement(
        episode.id,
        {
          acknowledgement_version: sentAckVersion,
          viewed_revision_no: sentViewedRev,
          ...(trimmedNote ? { note: trimmedNote } : {}),
        },
        csrf,
      )
      if (!mounted.current) return
      setAckUncertainVersion(null)
      setNote('')
      setAckTone('ok')
      setAckMsg(
        `Đã xác nhận episode #${episode.id} ở revision ${res.acknowledgement.acknowledged_revision_no} (ack version ${res.acknowledgement.acknowledgement_version}).`,
      )
      await loadDetail()
      if (activeAuditTab === 'acknowledgements') {
        void loadAuditTab('acknowledgements')
      }
    } catch (error) {
      if (!mounted.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setAckUncertainVersion(null)
        setAckTone('err')
        if (error.status === 409 && error.code === 'ALERT_VERSION_CONFLICT') {
          setAckMsg(
            'Phiên bản xác nhận hoặc revision vừa thay đổi trước khi gửi (409 ALERT_VERSION_CONFLICT). Đã tải lại dữ liệu mới nhất để bạn rà soát.',
          )
          await loadDetail()
        } else if (error.status === 409) {
          setAckMsg(error.message)
          await loadDetail()
        } else {
          setAckMsg(error.message)
        }
      } else {
        setAckUncertainVersion(sentAckVersion)
        setAckTone('warn')
        setAckMsg(
          `Chưa xác nhận được phản hồi từ máy chủ cho acknowledgement_version=${sentAckVersion}. Không tự gửi lại để tránh sai version — bấm nút kiểm tra exact acknowledgement_version bên dưới.`,
        )
      }
    } finally {
      operation.current = false
      if (mounted.current) setAckBusy(false)
    }
  }

  async function verifyUncertainAcknowledgement() {
    if (!detail || ackUncertainVersion === null || ackBusy) return
    setAckBusy(true)
    try {
      const res = await getAlertDetail(episodeId, {
        audit_kind: 'acknowledgements',
        acknowledgement_version: ackUncertainVersion,
      })
      if (!mounted.current) return
      setDetail(res)
      const matched =
        res.audit?.kind === 'acknowledgements' ? res.audit.items[0] ?? null : null
      setAckLookupResult(matched)
      if (matched) {
        setAckTone('ok')
        setAckMsg(
          `Đối soát thành công: acknowledgement_version=${ackUncertainVersion} ĐÃ ĐƯỢC GHI NHẬN bởi ${matched.admin_username ?? matched.admin_id} lúc ${formatUtcToVi(matched.acknowledged_at)}.`,
        )
        setAckUncertainVersion(null)
        setNote('')
      } else {
        setAckTone('info')
        setAckMsg(
          `Đối soát hoàn tất: chưa thấy bản ghi cho acknowledgement_version=${ackUncertainVersion}. Kiểm tra trạng thái hiện tại ở trên trước khi thao tác lại.`,
        )
        setAckUncertainVersion(null)
      }
    } catch (error) {
      if (!mounted.current) return
      setAckTone('err')
      setAckMsg(describeApiError(error, 'Không đối soát được trạng thái xác nhận.'))
    } finally {
      if (mounted.current) setAckBusy(false)
    }
  }

  const revisionColumns: Column<AlertRevisionView>[] = [
    {
      key: 'revision_no',
      header: 'Revision',
      className: 'tabular mono',
      render: r => (
        <div className="stack-sm">
          <strong>#{r.revision_no}</strong>
          <small className="muted">ack v{r.acknowledgement_version}</small>
        </div>
      ),
    },
    {
      key: 'direction',
      header: 'Chiều / Snapshot',
      className: 'tabular mono',
      render: r => (
        <div className="stack-sm">
          <span>{r.direction ?? 'TIMEOUT'}</span>
          <small className="muted">
            {r.timeout_snapshot !== null
              ? `T=${r.timeout_snapshot}m`
              : `${r.lower_snapshot ?? '—'} .. ${r.upper_snapshot ?? '—'}`}
          </small>
        </div>
      ),
    },
    {
      key: 'times',
      header: 'Mốc sự kiện / Đóng',
      className: 'tabular',
      render: r => (
        <div className="stack-sm">
          <small>
            Start: {r.start_event_time ? formatUtcToVi(r.start_event_time) : r.start_backend_at ? formatUtcToVi(r.start_backend_at) : '—'}
          </small>
          <small className="muted">
            Closed: {r.closed_at ? formatUtcToVi(r.closed_at) : 'Đang mở'}
          </small>
        </div>
      ),
    },
    {
      key: 'end_reason',
      header: 'Lý do kết thúc',
      render: r => formatEndReason(r.end_reason),
    },
    {
      key: 'meta',
      header: 'Generation / Metadata',
      className: 'mono',
      render: r => (
        <div className="stack-sm">
          <small>gen {r.evaluation_generation}</small>
          {r.threshold_metadata && (
            <small className="muted">
              codec={r.threshold_metadata.codec ?? '—'} ·{' '}
              {r.threshold_metadata.transition ?? '—'}
            </small>
          )}
        </div>
      ),
    },
  ]

  const ackColumns: Column<AlertAcknowledgementView>[] = [
    {
      key: 'ver',
      header: 'Ack Version',
      className: 'tabular mono',
      render: a => `v${a.acknowledgement_version} (rev #${a.acknowledged_revision_no})`,
    },
    {
      key: 'admin',
      header: 'Quản trị viên',
      render: a => a.admin_username ?? a.admin_id,
    },
    {
      key: 'at',
      header: 'Thời điểm (+07:00)',
      className: 'tabular',
      render: a => formatUtcToVi(a.acknowledged_at),
    },
    {
      key: 'note',
      header: 'Ghi chú',
      render: a => a.note || '—',
    },
  ]

  const replacementColumns: Column<AlertReplacementView>[] = [
    {
      key: 'reason',
      header: 'Loại thay thế',
      render: r => <StatusBadge tone="neutral" label={formatReplacementReason(r.reason)} />,
    },
    {
      key: 'old',
      header: 'Episode cũ',
      className: 'mono',
      render: r => (
        <Link to={`/alerts/${encodeURIComponent(r.old_episode_id)}`}>
          #{r.old_episode_id} (rev {r.old_revision_no})
        </Link>
      ),
    },
    {
      key: 'new',
      header: 'Episode mới',
      className: 'mono',
      render: r => (
        <Link to={`/alerts/${encodeURIComponent(r.new_episode_id)}`}>
          #{r.new_episode_id} (rev {r.new_revision_no})
        </Link>
      ),
    },
    {
      key: 'created_at',
      header: 'Ghi nhận lúc',
      className: 'tabular',
      render: r => formatUtcToVi(r.created_at),
    },
    {
      key: 'gen',
      header: 'Generation',
      className: 'mono',
      render: r => r.evaluation_generation,
    },
  ]

  const episode = detail?.episode
  const isCrossFarm =
    Boolean(episode && selectedFarmId && episode.farm.id !== selectedFarmId)
  const canSwitchToEpisodeFarm = Boolean(
    episode && selectFarm && farms.some(f => f.id === episode.farm.id),
  )

  return (
    <div className="stack" aria-label="Chi tiết episode cảnh báo">
      <PageHeader
        eyebrow="Vận hành · FL-11 Chi tiết & Xác nhận"
        title={
          episode
            ? `Episode #${episode.id} · ${formatAlertTitle(episode)}`
            : `Chi tiết Episode #${episodeId}`
        }
        description={
          episode
            ? `Farm ${episode.farm.code} (${episode.farm.name}) · Zone ${episode.zone.code} · Node ${episode.node.device_id} (${episode.node.name})`
            : 'Tra cứu toàn cục thông tin episode, bằng chứng revision và audit lineage.'
        }
        actions={
          <div className="row">
            <Link className="btn btn-secondary" to="/alerts">
              ← Danh sách cảnh báo
            </Link>
            {episode && (
              <Link
                className="btn btn-secondary"
                to={`/monitoring?node_id=${encodeURIComponent(episode.node.id)}`}
              >
                Cấu hình giám sát Node
              </Link>
            )}
            <button type="button" className="btn-secondary" disabled={loading} onClick={() => void loadDetail()}>
              Làm mới
            </button>
          </div>
        }
      />

      {loadError && <StateBanner tone="err" message={loadError} />}

      {loading && !detail ? (
        <div className="card">
          <p className="muted" role="status">Đang tải chi tiết episode cảnh báo…</p>
        </div>
      ) : detail && episode ? (
        <>
          {/* Banner Cross-farm (G4-D02): không tự đổi selector Farm */}
          {isCrossFarm && (
            <StateBanner
              tone="info"
              message={`Deep-link chéo Farm: episode này thuộc Farm ${episode.farm.code} (${episode.farm.name}), khác với Farm đang chọn (${selectedFarm?.code ?? selectedFarmId}). Bối cảnh Topbar được giữ nguyên cho tới khi bạn bấm chuyển.`}
              action={
                canSwitchToEpisodeFarm && selectFarm ? (
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => selectFarm(episode.farm.id)}
                  >
                    Chuyển bối cảnh sang Farm {episode.farm.code}
                  </button>
                ) : undefined
              }
            />
          )}

          {/* Banner Retired episode */}
          {!episode.is_current && (
            <StateBanner
              tone="warn"
              message="Episode này đã được đánh dấu thay thế (is_current = false / Retired) trong quá trình replay/reconcile. Mọi thao tác xác nhận (acknowledgement) bị khóa; xem tab Thay thế (replacements) để theo dấu episode kế nhiệm."
              action={
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  onClick={() => void loadAuditTab('replacements')}
                >
                  Xem lịch sử thay thế
                </button>
              }
            />
          )}

          {/* Tổng quan trạng thái & Đối soát */}
          <div className="kpi-grid">
            <div className="card kpi-card">
              <small className="muted">Vòng đời &amp; Phục hồi</small>
              <div className="row">
                <StatusBadge
                  tone={episode.lifecycle_status === 'OPEN' ? 'err' : episode.recovery_proven === false ? 'warn' : 'ok'}
                  label={
                    episode.lifecycle_status === 'OPEN'
                      ? 'OPEN'
                      : episode.recovery_proven === false
                        ? 'CLOSED · Chờ phục hồi'
                        : 'CLOSED · Đã phục hồi'
                  }
                />
                {!episode.is_current && <StatusBadge tone="neutral" label="Retired" />}
              </div>
              <small className="muted">{formatEndReason(episode.end_reason)}</small>
            </div>

            <div className="card kpi-card">
              <small className="muted">Xác nhận hiện hành</small>
              <div className="row">
                <StatusBadge
                  tone={episode.acknowledged ? 'ok' : 'warn'}
                  label={episode.acknowledged ? 'Đã xác nhận' : 'Chưa xác nhận'}
                />
                <span className="mono">Ack v{episode.acknowledgement_version}</span>
              </div>
              <small className="muted">
                Revision hiện tại #{episode.current_revision_no} · as_of {formatUtcToVi(detail.as_of)}
              </small>
            </div>

            <div className="card kpi-card">
              <small className="muted">Đối soát Node (evaluation)</small>
              <div className="row">
                {evaluationBadge(detail.evaluation.status)}
                <span className="mono">
                  {detail.evaluation.completed_generation}/{detail.evaluation.desired_generation}
                </span>
              </div>
              <small className="muted">
                {detail.evaluation.last_error_code
                  ? `Lỗi gần nhất: ${detail.evaluation.last_error_code}`
                  : detail.evaluation.last_success_at
                    ? `Hoàn tất: ${formatUtcToVi(detail.evaluation.last_success_at)}`
                    : 'Chưa có mốc hoàn tất'}
              </small>
            </div>
          </div>

          {/* Bằng chứng revision bất biến & Form Xác nhận */}
          <div className="dashboard-split">
            <section className="card stack" aria-label="Bằng chứng revision hiện tại">
              <div className="card-header">
                <h2>Bằng chứng Revision #{episode.current_revision.revision_no}</h2>
                <span className="mono">gen {episode.current_revision.evaluation_generation}</span>
              </div>

              <dl className="kv-grid">
                <div className="kv-item">
                  <dt>Thời điểm phát hiện (first_detected_at)</dt>
                  <dd className="tabular">{formatUtcToVi(episode.first_detected_at)}</dd>
                </div>
                <div className="kv-item">
                  <dt>Thời điểm đóng (closed_at)</dt>
                  <dd className="tabular">
                    {episode.closed_at ? formatUtcToVi(episode.closed_at) : 'Đang mở'}
                  </dd>
                </div>
                <div className="kv-item">
                  <dt>Snapshot ngưỡng / Timeout</dt>
                  <dd className="mono">
                    {episode.current_revision.timeout_snapshot !== null
                      ? `T = ${episode.current_revision.timeout_snapshot} phút`
                      : `${episode.current_revision.lower_snapshot ?? '—'} .. ${episode.current_revision.upper_snapshot ?? '—'}`}
                  </dd>
                </div>
                <div className="kv-item">
                  <dt>Config ID / Period ID</dt>
                  <dd className="mono">
                    cfg {episode.current_revision.config_id}
                    {episode.current_revision.period_id
                      ? ` · period ${episode.current_revision.period_id}`
                      : ''}
                  </dd>
                </div>
                <div className="kv-item">
                  <dt>Measurement mở / đóng</dt>
                  <dd className="mono">
                    start={episode.current_revision.start_measurement_id ?? '—'} · end=
                    {episode.current_revision.end_measurement_id ?? '—'}
                  </dd>
                </div>
                <div className="kv-item">
                  <dt>Event time / Backend at</dt>
                  <dd className="tabular">
                    {episode.current_revision.start_event_time
                      ? `start ${formatUtcToVi(episode.current_revision.start_event_time)}`
                      : episode.current_revision.start_backend_at
                        ? `backend ${formatUtcToVi(episode.current_revision.start_backend_at)}`
                        : '—'}
                    {episode.current_revision.end_event_time
                      ? ` → end ${formatUtcToVi(episode.current_revision.end_event_time)}`
                      : episode.current_revision.end_backend_at
                        ? ` → backend ${formatUtcToVi(episode.current_revision.end_backend_at)}`
                        : ''}
                  </dd>
                </div>
                <div className="kv-item">
                  <dt>Đánh giá lúc (evaluated_at)</dt>
                  <dd className="tabular">
                    {episode.current_revision.evaluated_at
                      ? formatUtcToVi(episode.current_revision.evaluated_at)
                      : '—'}
                  </dd>
                </div>
                <div className="kv-item">
                  <dt>Metadata replay ngưỡng</dt>
                  <dd className="mono">
                    {episode.current_revision.threshold_metadata
                      ? `codec=${episode.current_revision.threshold_metadata.codec ?? '—'}, transition=${episode.current_revision.threshold_metadata.transition ?? '—'}, current=${String(episode.current_revision.threshold_metadata.is_current)}`
                      : '—'}
                  </dd>
                </div>
              </dl>
            </section>

            <section className="card stack" aria-label="Xác nhận cảnh báo">
              <div className="card-header">
                <h2>Xác nhận (Acknowledgement)</h2>
                <span className="mono">
                  v{episode.acknowledgement_version} · rev #{episode.current_revision.revision_no}
                </span>
              </div>

              {ackMsg && <StateBanner tone={ackTone} message={ackMsg} />}

              {ackUncertainVersion !== null && (
                <div className="row">
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={ackBusy}
                    onClick={() => void verifyUncertainAcknowledgement()}
                  >
                    {ackBusy
                      ? 'Đang đối soát…'
                      : `Kiểm tra kết quả xác nhận (ack_version=${ackUncertainVersion})`}
                  </button>
                </div>
              )}

              {ackLookupResult && (
                <StateBanner
                  tone="info"
                  message={`Bản ghi xác nhận tìm thấy: v${ackLookupResult.acknowledgement_version} trên revision #${ackLookupResult.acknowledged_revision_no} bởi ${ackLookupResult.admin_username ?? ackLookupResult.admin_id}.`}
                />
              )}

              {episode.current_acknowledgement && (
                <div className="card stack-sm" style={{ background: 'var(--color-surface-alt)' }}>
                  <strong>Xác nhận hiệu lực cho v{episode.acknowledgement_version}</strong>
                  <small className="tabular">
                    Người xác nhận:{' '}
                    <strong>
                      {episode.current_acknowledgement.admin_username ??
                        episode.current_acknowledgement.admin_id}
                    </strong>{' '}
                    lúc {formatUtcToVi(episode.current_acknowledgement.acknowledged_at)} (trên rev #
                    {episode.current_acknowledgement.acknowledged_revision_no})
                  </small>
                  <small>
                    Ghi chú: {episode.current_acknowledgement.note || 'Không có ghi chú'}
                  </small>
                </div>
              )}

              <div className="stack-sm">
                <label>
                  Ghi chú xác nhận (tùy chọn, plain text tối đa 1000 ký tự)
                  <textarea
                    rows={3}
                    maxLength={1000}
                    disabled={
                      ackBusy ||
                      !canWrite() ||
                      !episode.is_current ||
                      episode.acknowledged
                    }
                    placeholder={
                      !episode.is_current
                        ? 'Episode đã Retired — không thể xác nhận'
                        : episode.acknowledged
                          ? 'Phiên bản hiện tại đã được xác nhận'
                          : 'Nhập ghi chú xử lý của kỹ thuật viên…'
                    }
                    value={note}
                    onChange={e => setNote(e.target.value)}
                  />
                </label>
                <div className="row-between">
                  <small className="muted tabular">{note.trim().length}/1000 ký tự</small>
                  <button
                    type="button"
                    disabled={
                      ackBusy ||
                      !canWrite() ||
                      !episode.is_current ||
                      episode.acknowledged
                    }
                    onClick={() => void submitAcknowledgement()}
                  >
                    {ackBusy ? 'Đang gửi xác nhận…' : 'Xác nhận cảnh báo'}
                  </button>
                </div>
              </div>
            </section>
          </div>

          {/* 3 tab audit lazy-load theo FL-11 */}
          <section className="card stack" aria-label="Nhật ký kiểm toán (Audit Lineage)">
            <div className="card-header">
              <h2>Audit Lineage (Lazy-load)</h2>
              <small className="muted">
                Chỉ tải từng tab khi người vận hành mở theo hợp đồng GET /alerts/&#123;id&#125;?audit_kind=…
              </small>
            </div>

            <div className="tabs" role="tablist" aria-label="Nhật ký audit episode">
              <button
                type="button"
                role="tab"
                aria-selected={activeAuditTab === 'revisions'}
                className={`tab-btn${activeAuditTab === 'revisions' ? ' active' : ''}`}
                onClick={() => void loadAuditTab('revisions')}
              >
                Revisions (Lịch sử sửa đổi)
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeAuditTab === 'acknowledgements'}
                className={`tab-btn${activeAuditTab === 'acknowledgements' ? ' active' : ''}`}
                onClick={() => void loadAuditTab('acknowledgements')}
              >
                Acknowledgements (Lịch sử xác nhận)
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeAuditTab === 'replacements'}
                className={`tab-btn${activeAuditTab === 'replacements' ? ' active' : ''}`}
                onClick={() => void loadAuditTab('replacements')}
              >
                Replacements (Thay thế / Lineage)
              </button>
            </div>

            {auditError && <StateBanner tone="err" message={auditError} />}

            {activeAuditTab === null ? (
              <EmptyState
                title="Chọn một tab audit để tải dữ liệu"
                description="Chọn Revisions, Acknowledgements hoặc Replacements ở trên để tải theo trang (cursor)."
              />
            ) : activeAuditTab === 'revisions' ? (
              <>
                <DataTable
                  columns={revisionColumns}
                  items={auditRevisions}
                  getKey={r => String(r.revision_no)}
                  loading={auditLoading}
                  loaded={auditLoadedKind === 'revisions'}
                  emptyTitle="Chưa có revision nào khác"
                />
                <CursorPager
                  count={auditRevisions.length}
                  nextCursor={auditCursor}
                  busy={auditLoading}
                  onLoadMore={() => auditCursor && void loadAuditTab('revisions', auditCursor)}
                />
              </>
            ) : activeAuditTab === 'acknowledgements' ? (
              <>
                <DataTable
                  columns={ackColumns}
                  items={auditAcks}
                  getKey={a => `${a.episode_id}:${a.acknowledgement_version}`}
                  loading={auditLoading}
                  loaded={auditLoadedKind === 'acknowledgements'}
                  emptyTitle="Chưa có lịch sử xác nhận cho episode này"
                />
                <CursorPager
                  count={auditAcks.length}
                  nextCursor={auditCursor}
                  busy={auditLoading}
                  onLoadMore={() =>
                    auditCursor && void loadAuditTab('acknowledgements', auditCursor)
                  }
                />
              </>
            ) : (
              <>
                <DataTable
                  columns={replacementColumns}
                  items={auditReplacements}
                  getKey={r =>
                    `${r.old_episode_id}:${r.old_revision_no}->${r.new_episode_id}:${r.new_revision_no}`
                  }
                  loading={auditLoading}
                  loaded={auditLoadedKind === 'replacements'}
                  emptyTitle="Không có liên kết thay thế (Split / Merge / Rewrite)"
                />
                <CursorPager
                  count={auditReplacements.length}
                  nextCursor={auditCursor}
                  busy={auditLoading}
                  onLoadMore={() =>
                    auditCursor && void loadAuditTab('replacements', auditCursor)
                  }
                />
              </>
            )}
          </section>
        </>
      ) : null}
    </div>
  )
}
