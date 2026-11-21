import {
  listAlerts,
} from '../../api/alerts'
import type {
  AlertDirection,
  AlertKind,
  AlertLifecycle,
  AlertSummaryView,
  AlertsListParams,
} from '../../api/alerts'
import type { EvaluationView } from '../../api/monitoring'
import { describeApiError, isAbortOrStale } from '../measurements/scopeLoader'
import { METRIC_META, viDatetimeInputToIso } from '../measurements/format'

export type AlertFilterDraft = {
  view: 'active' | 'history'
  node_id: string
  kind: '' | AlertKind
  lifecycle: '' | AlertLifecycle
  acknowledged: '' | 'true' | 'false'
  recovery_proven: '' | 'true' | 'false'
  fromLocal: string
  toLocal: string
}

export function createDefaultAlertFilterDraft(
  view: 'active' | 'history' = 'active',
  nodeId = '',
): AlertFilterDraft {
  return {
    view,
    node_id: nodeId,
    kind: '',
    lifecycle: '',
    acknowledged: '',
    recovery_proven: '',
    fromLocal: '',
    toLocal: '',
  }
}

export type FilterValidationResult =
  | { ok: true; fromIso?: string; toIso?: string }
  | { ok: false; error: string }

export function validateAlertFilterDraft(draft: AlertFilterDraft): FilterValidationResult {
  if (draft.view === 'active' && draft.lifecycle === 'CLOSED') {
    return {
      ok: false,
      error: 'Tab Đang hiệu lực (active) không thể lọc lifecycle = CLOSED.',
    }
  }
  if (draft.kind === 'TIMEOUT' && draft.recovery_proven !== '') {
    return {
      ok: false,
      error: 'Bộ lọc recovery_proven không hợp lệ khi đang lọc loại cảnh báo TIMEOUT.',
    }
  }

  let fromIso: string | undefined
  let toIso: string | undefined
  if (draft.view === 'history') {
    const trimmedFrom = draft.fromLocal.trim()
    const trimmedTo = draft.toLocal.trim()
    if (trimmedFrom) {
      try {
        fromIso = viDatetimeInputToIso(trimmedFrom)
      } catch {
        return { ok: false, error: 'Thời gian Từ không hợp lệ.' }
      }
    }
    if (trimmedTo) {
      try {
        toIso = viDatetimeInputToIso(trimmedTo)
      } catch {
        return { ok: false, error: 'Thời gian Đến không hợp lệ.' }
      }
    }
    if (fromIso && toIso && new Date(fromIso).getTime() > new Date(toIso).getTime()) {
      return { ok: false, error: 'Mốc Từ phải nhỏ hơn hoặc bằng mốc Đến.' }
    }
  }

  return { ok: true, fromIso, toIso }
}

export function buildAlertsListParams(
  farmId: string,
  draft: AlertFilterDraft,
  validated: { fromIso?: string; toIso?: string },
  options: { pageSize?: number; cursor?: string } = {},
): AlertsListParams {
  const params: AlertsListParams = {
    farm_id: farmId,
    view: draft.view,
    page_size: options.pageSize ?? 20,
  }
  if (draft.node_id) params.node_id = draft.node_id
  if (draft.kind) params.kind = draft.kind
  if (draft.lifecycle) params.lifecycle = draft.lifecycle
  if (draft.acknowledged) params.acknowledged = draft.acknowledged
  if (draft.recovery_proven) params.recovery_proven = draft.recovery_proven
  if (draft.view === 'history') {
    if (validated.fromIso) params.from = validated.fromIso
    if (validated.toIso) params.to = validated.toIso
  }
  if (options.cursor) params.cursor = options.cursor
  return params
}

export type AlertsStoreState = {
  items: AlertSummaryView[]
  nextCursor: string | null
  asOf: string | null
  range: { from: string | null; to: string | null }
  evaluationByNode: Record<string, EvaluationView>
  loading: boolean
  loadingMore: boolean
  loaded: boolean
  error: string
}

export type AlertsFetcher = (
  params: AlertsListParams,
  signal?: AbortSignal,
) => Promise<{
  as_of: string
  range: { from: string | null; to: string | null }
  evaluation_by_node: Record<string, EvaluationView>
  items: AlertSummaryView[]
  next_cursor: string | null
}>

export class AlertsStore {
  private state: AlertsStoreState = {
    items: [],
    nextCursor: null,
    asOf: null,
    range: { from: null, to: null },
    evaluationByNode: {},
    loading: false,
    loadingMore: false,
    loaded: false,
    error: '',
  }
  private generation = 0
  private controller: AbortController | null = null
  private lastParams: AlertsListParams | null = null
  private readonly listeners = new Set<(state: AlertsStoreState) => void>()
  private readonly fetcher: AlertsFetcher

  constructor(fetcher: AlertsFetcher = listAlerts) {
    this.fetcher = fetcher
  }

  getState(): AlertsStoreState {
    return this.state
  }

  subscribe(listener: (state: AlertsStoreState) => void): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private emit(patch: Partial<AlertsStoreState>) {
    this.state = { ...this.state, ...patch }
    for (const listener of this.listeners) {
      listener(this.state)
    }
  }

  reset() {
    this.generation += 1
    this.controller?.abort()
    this.controller = null
    this.lastParams = null
    this.emit({
      items: [],
      nextCursor: null,
      asOf: null,
      range: { from: null, to: null },
      evaluationByNode: {},
      loading: false,
      loadingMore: false,
      loaded: false,
      error: '',
    })
  }

  async load(params: AlertsListParams): Promise<void> {
    this.generation += 1
    const myGen = this.generation
    this.controller?.abort()
    const controller = new AbortController()
    this.controller = controller
    const nextParams: AlertsListParams = { ...params, cursor: undefined }
    this.lastParams = nextParams

    this.emit({
      loading: true,
      loadingMore: false,
      error: '',
    })

    try {
      const res = await this.fetcher(nextParams, controller.signal)
      if (myGen !== this.generation || controller.signal.aborted) return
      this.emit({
        items: res.items,
        nextCursor: res.next_cursor,
        asOf: res.as_of,
        range: res.range,
        evaluationByNode: res.evaluation_by_node,
        loading: false,
        loadingMore: false,
        loaded: true,
        error: '',
      })
    } catch (error) {
      if (myGen !== this.generation || isAbortOrStale(error)) return
      this.emit({
        loading: false,
        loadingMore: false,
        error: describeApiError(error, 'Không tải được danh sách cảnh báo.'),
      })
    }
  }

  async loadMore(): Promise<void> {
    if (!this.lastParams || !this.state.nextCursor || this.state.loading || this.state.loadingMore) {
      return
    }
    const myGen = this.generation
    const controller = new AbortController()
    this.controller = controller

    this.emit({ loadingMore: true, error: '' })
    try {
      const res = await this.fetcher(
        { ...this.lastParams, cursor: this.state.nextCursor },
        controller.signal,
      )
      if (myGen !== this.generation || controller.signal.aborted) return
      const seen = new Set(this.state.items.map(item => item.id))
      const merged = [...this.state.items, ...res.items.filter(item => !seen.has(item.id))]
      this.emit({
        items: merged,
        nextCursor: res.next_cursor,
        asOf: res.as_of,
        range: res.range,
        evaluationByNode: res.evaluation_by_node,
        loadingMore: false,
        loaded: true,
        error: '',
      })
    } catch (error) {
      if (myGen !== this.generation || isAbortOrStale(error)) return
      this.emit({
        loadingMore: false,
        error: describeApiError(error, 'Không tải thêm được cảnh báo.'),
      })
    }
  }

  dispose() {
    this.generation += 1
    this.controller?.abort()
    this.controller = null
  }
}

export function formatAlertTitle(alert: {
  kind: AlertKind
  metric_code: string | null
  direction: AlertDirection | null
}): string {
  if (alert.kind === 'TIMEOUT') {
    return 'TIMEOUT · Quá hạn lưu bản đo mới'
  }
  const meta = alert.metric_code
    ? METRIC_META.find(m => m.code === alert.metric_code)
    : undefined
  const metricLabel = meta ? `${meta.label} (${alert.metric_code})` : alert.metric_code ?? 'Ngưỡng'
  const dirLabel = alert.direction === 'HIGH' ? 'Cao (HIGH)' : alert.direction === 'LOW' ? 'Thấp (LOW)' : ''
  return dirLabel ? `NGƯỠNG · ${metricLabel} · ${dirLabel}` : `NGƯỠNG · ${metricLabel}`
}

export function formatEndReason(reason: string | null): string {
  switch (reason) {
    case null:
      return '—'
    case 'RECOVERED':
      return 'Đã phục hồi (RECOVERED)'
    case 'CONFIG_CHANGED':
      return 'Đổi ngưỡng / tắt ngưỡng (CONFIG_CHANGED)'
    case 'RECEIVING_DISABLED':
      return 'Tắt quyền tiếp nhận (RECEIVING_DISABLED)'
    case 'REPLAY_REMOVED':
      return 'Bị loại khi replay lịch sử (REPLAY_REMOVED)'
    case 'ZONE_ARCHIVED':
      return 'Zone đã lưu trữ (ZONE_ARCHIVED)'
    case 'FARM_ARCHIVED':
      return 'Farm đã lưu trữ (FARM_ARCHIVED)'
    default:
      return reason
  }
}

export function formatReplacementReason(reason: string): string {
  switch (reason) {
    case 'SPLIT':
      return 'Tách episode (SPLIT)'
    case 'MERGE':
      return 'Gộp episode (MERGE)'
    case 'REWRITE':
      return 'Viết lại khi replay (REWRITE)'
    case 'REPLACED':
      return 'Thay thế (REPLACED)'
    default:
      return reason
  }
}

export function summarizeEvaluationMap(evaluationByNode: Record<string, EvaluationView>): {
  total: number
  upToDate: number
  pending: number
  error: number
} {
  const values = Object.values(evaluationByNode)
  let upToDate = 0
  let pending = 0
  let error = 0
  for (const item of values) {
    if (item.status === 'UP_TO_DATE') upToDate += 1
    else if (item.status === 'PENDING') pending += 1
    else if (item.status === 'ERROR') error += 1
  }
  return {
    total: values.length,
    upToDate,
    pending,
    error,
  }
}
