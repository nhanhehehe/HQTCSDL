import type { MetricCode } from '../features/measurements/types'
import { getJson, mutationHeaders, requestJson } from './client'
import type { EvaluationView } from './monitoring'

export type AlertKind = 'THRESHOLD' | 'TIMEOUT'
export type AlertLifecycle = 'OPEN' | 'CLOSED'
export type AlertDirection = 'LOW' | 'HIGH'
export type AuditKind = 'revisions' | 'acknowledgements' | 'replacements'

export type AlertThresholdMetadata = {
  codec: number | null
  is_current: boolean | null
  transition: string | null
}

export type AlertRevisionView = {
  revision_no: number
  config_id: string
  period_id: string | null
  direction: AlertDirection | null
  start_event_time: string | null
  end_event_time: string | null
  start_backend_at: string | null
  end_backend_at: string | null
  closed_at: string | null
  end_reason: string | null
  start_measurement_id: string | null
  end_measurement_id: string | null
  lower_snapshot: string | null
  upper_snapshot: string | null
  timeout_snapshot: number | null
  evaluated_at: string | null
  evaluation_generation: string
  acknowledgement_version: number
  threshold_metadata: AlertThresholdMetadata | null
}

export type AlertAcknowledgementView = {
  episode_id: string
  acknowledgement_version: number
  acknowledged_revision_no: number
  admin_id: string
  admin_username: string | null
  acknowledged_at: string
  note: string | null
}

export type AlertReplacementView = {
  old_episode_id: string
  old_revision_no: number
  new_episode_id: string
  new_revision_no: number
  created_at: string
  evaluation_generation: string
  reason: 'SPLIT' | 'MERGE' | 'REWRITE' | 'REPLACED'
}

export type AlertSummaryView = {
  id: string
  node_id: string
  device_id: string
  node_name: string
  zone_id: string
  zone_code: string
  farm_id: string
  kind: AlertKind
  metric_code: MetricCode | null
  direction: AlertDirection | null
  lifecycle_status: AlertLifecycle
  is_current: boolean
  current_revision_no: number
  acknowledgement_version: number
  acknowledged: boolean
  current_acknowledgement: AlertAcknowledgementView | null
  recovery_proven: boolean | null
  first_detected_at: string
  closed_at: string | null
  end_reason: string | null
  current_revision: AlertRevisionView
}

export type AlertDetailView = AlertSummaryView & {
  farm: {
    id: string
    code: string
    name: string
    archived_at: string | null
  }
  zone: {
    id: string
    code: string
    name: string
    archived_at: string | null
  }
  node: {
    id: string
    device_id: string
    name: string
  }
}

export type AlertsListResponse = {
  as_of: string
  scope: {
    kind: 'FARM' | 'NODE'
    farm_id: string
    farm: {
      id: string
      code: string
      name: string
      archived_at: string | null
    }
    node_id: string | null
  }
  view: 'active' | 'history'
  range: {
    from: string | null
    to: string | null
  }
  evaluation_by_node: Record<string, EvaluationView>
  items: AlertSummaryView[]
  next_cursor: string | null
}

export type AlertAuditPage =
  | { kind: 'revisions'; items: AlertRevisionView[]; next_cursor: string | null }
  | { kind: 'acknowledgements'; items: AlertAcknowledgementView[]; next_cursor: string | null }
  | { kind: 'replacements'; items: AlertReplacementView[]; next_cursor: string | null }

export type AlertDetailResponse = {
  as_of: string
  episode: AlertDetailView
  evaluation: EvaluationView
  audit: AlertAuditPage | null
}

export type AlertCurrentState = {
  episode_id: string
  is_current: boolean
  lifecycle_status: AlertLifecycle
  current_revision_no: number
  acknowledgement_version: number
  acknowledged: boolean
}

export type AlertAckResponse = {
  acknowledgement: AlertAcknowledgementView
  current_state: AlertCurrentState
}

export type AlertsListParams = {
  farm_id: string
  node_id?: string
  view?: 'active' | 'history'
  kind?: AlertKind
  lifecycle?: AlertLifecycle
  acknowledged?: 'true' | 'false'
  recovery_proven?: 'true' | 'false'
  from?: string
  to?: string
  page_size?: number
  cursor?: string
}

export function listAlerts(
  params: AlertsListParams,
  signal?: AbortSignal,
): Promise<AlertsListResponse> {
  const qs = new URLSearchParams({ farm_id: params.farm_id })
  if (params.node_id) qs.set('node_id', params.node_id)
  if (params.view) qs.set('view', params.view)
  if (params.kind) qs.set('kind', params.kind)
  if (params.lifecycle) qs.set('lifecycle', params.lifecycle)
  if (params.acknowledged) qs.set('acknowledged', params.acknowledged)
  if (params.recovery_proven) qs.set('recovery_proven', params.recovery_proven)
  if (params.from) qs.set('from', params.from)
  if (params.to) qs.set('to', params.to)
  if (params.page_size !== undefined) qs.set('page_size', String(params.page_size))
  if (params.cursor) qs.set('cursor', params.cursor)
  return getJson<AlertsListResponse>(`/api/v1/alerts?${qs}`, signal)
}

export function getAlertDetail(
  episodeId: string,
  params: {
    audit_kind?: AuditKind
    audit_cursor?: string
    audit_page_size?: number
    acknowledgement_version?: number
  } = {},
  signal?: AbortSignal,
): Promise<AlertDetailResponse> {
  const qs = new URLSearchParams()
  if (params.audit_kind) qs.set('audit_kind', params.audit_kind)
  if (params.audit_cursor) qs.set('audit_cursor', params.audit_cursor)
  if (params.audit_page_size !== undefined) {
    qs.set('audit_page_size', String(params.audit_page_size))
  }
  if (params.acknowledgement_version !== undefined) {
    qs.set('acknowledgement_version', String(params.acknowledgement_version))
  }
  const suffix = qs.toString() ? `?${qs}` : ''
  return getJson<AlertDetailResponse>(
    `/api/v1/alerts/${encodeURIComponent(episodeId)}${suffix}`,
    signal,
  )
}

export function postAlertAcknowledgement(
  episodeId: string,
  body: {
    acknowledgement_version: number
    viewed_revision_no: number
    note?: string | null
  },
  csrf: string,
  signal?: AbortSignal,
): Promise<AlertAckResponse> {
  return requestJson<AlertAckResponse>(
    `/api/v1/alerts/${encodeURIComponent(episodeId)}/acknowledgements`,
    {
      method: 'POST',
      headers: mutationHeaders(csrf),
      body: JSON.stringify(body),
      signal,
    },
  )
}
