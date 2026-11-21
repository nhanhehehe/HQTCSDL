import type { MetricCode } from '../features/measurements/types'
import { getJson, mutationHeaders, requestJson } from './client'

export type MetricThresholdState = 'UNCONFIGURED' | 'DISABLED' | 'ACTIVE' | 'PAUSED'
export type TimeoutState = 'UNCONFIGURED' | 'ACTIVE' | 'PAUSED'
export type EvaluationStatus = 'UP_TO_DATE' | 'PENDING' | 'ERROR'
export type TimeoutValidationState = 'READY' | 'PROFILE_MISSING' | 'TIMEOUT_PROFILE_INCOMPATIBLE'

export type EvaluationView = {
  desired_generation: string
  completed_generation: string
  status: EvaluationStatus
  last_success_at: string | null
  last_error_code: string | null
}

export type TimeoutValidationView = {
  state: TimeoutValidationState
  sampling_interval_seconds: number | null
  normal_delivery_budget_seconds: number | null
  min_timeout_minutes: number | null
}

export type ThresholdSettingView = {
  metric_code: MetricCode
  enabled: boolean
  lower: string | null
  upper: string | null
  state: MetricThresholdState
}

export type MonitoringStateView = {
  node_id: string
  threshold_config_id: string | null
  timeout_config_id: string | null
  thresholds: ThresholdSettingView[]
  timeout_minutes: number | null
  timeout_state: TimeoutState
  S: string | null
  L: string | null
  active_timeout_period_id: string | null
  evaluation: EvaluationView
  timeout_validation: TimeoutValidationView
}

export type NodeReceivingPatchResponse = {
  profile_row_version: string
  receiving_enabled: boolean
  monitoring: MonitoringStateView
  evaluation: EvaluationView
}

export type GatewayReceivingPatchResponse = {
  profile_row_version: string
  receiving_enabled: boolean
  affected_nodes: MonitoringStateView[]
}

export type MetricThresholdPatchInput = {
  enabled: boolean
  lower?: string
  upper?: string
}

export type MonitoringPatchPayload = {
  expected_threshold_config_id?: string | null
  thresholds?: Partial<Record<MetricCode, MetricThresholdPatchInput>>
  expected_timeout_config_id?: string | null
  timeout_minutes?: number
}

const EXACT_DECIMAL_9_2_RE = /^-?(?:0|[1-9]\d{0,6})(?:\.\d{1,2})?$/

export function isValidExactDecimal92(raw: string): boolean {
  const trimmed = raw.trim()
  if (!EXACT_DECIMAL_9_2_RE.test(trimmed)) return false
  const num = Number(trimmed)
  return Number.isFinite(num) && Math.abs(num) <= 9999999.99
}

export function compareDecimalStrings(a: string, b: string): number {
  const parseScaled = (text: string): bigint => {
    const trimmed = text.trim()
    const neg = trimmed.startsWith('-')
    const unsigned = neg ? trimmed.slice(1) : trimmed
    const [intPart, fracPart = ''] = unsigned.split('.')
    const scaled = BigInt(intPart + fracPart.padEnd(2, '0'))
    return neg ? -scaled : scaled
  }
  const sa = parseScaled(a)
  const sb = parseScaled(b)
  if (sa < sb) return -1
  if (sa > sb) return 1
  return 0
}

export function serializeMonitoringPatchJson(payload: MonitoringPatchPayload): string {
  const parts: string[] = []
  if (payload.thresholds !== undefined) {
    parts.push(
      `"expected_threshold_config_id":${JSON.stringify(payload.expected_threshold_config_id ?? null)}`,
    )
    const metricEntries: string[] = []
    for (const [metricCode, item] of Object.entries(payload.thresholds)) {
      if (!item) continue
      const inner: string[] = [`"enabled":${item.enabled ? 'true' : 'false'}`]
      if (item.lower !== undefined) {
        const trimmed = item.lower.trim()
        if (!isValidExactDecimal92(trimmed)) {
          throw new Error(`THRESHOLD_DECIMAL_INVALID:thresholds.${metricCode}.lower`)
        }
        inner.push(`"lower":${trimmed}`)
      }
      if (item.upper !== undefined) {
        const trimmed = item.upper.trim()
        if (!isValidExactDecimal92(trimmed)) {
          throw new Error(`THRESHOLD_DECIMAL_INVALID:thresholds.${metricCode}.upper`)
        }
        inner.push(`"upper":${trimmed}`)
      }
      metricEntries.push(`${JSON.stringify(metricCode)}:{${inner.join(',')}}`)
    }
    parts.push(`"thresholds":{${metricEntries.join(',')}}`)
  }
  if (payload.timeout_minutes !== undefined) {
    parts.push(
      `"expected_timeout_config_id":${JSON.stringify(payload.expected_timeout_config_id ?? null)}`,
    )
    parts.push(`"timeout_minutes":${payload.timeout_minutes}`)
  }
  return `{${parts.join(',')}}`
}

export function getNodeMonitoring(nodeId: string, signal?: AbortSignal): Promise<MonitoringStateView> {
  return getJson<MonitoringStateView>(`/api/v1/nodes/${encodeURIComponent(nodeId)}/monitoring`, signal)
}

export function patchNodeMonitoring(
  nodeId: string,
  payload: MonitoringPatchPayload,
  csrf: string,
  signal?: AbortSignal,
): Promise<MonitoringStateView> {
  return requestJson<MonitoringStateView>(
    `/api/v1/nodes/${encodeURIComponent(nodeId)}/monitoring`,
    {
      method: 'PATCH',
      headers: mutationHeaders(csrf),
      body: serializeMonitoringPatchJson(payload),
      signal,
    },
  )
}

export function patchNodeReceiving(
  nodeId: string,
  body: { receiving_enabled: boolean; expected_row_version: string },
  csrf: string,
  signal?: AbortSignal,
): Promise<NodeReceivingPatchResponse> {
  return requestJson<NodeReceivingPatchResponse>(
    `/api/v1/nodes/${encodeURIComponent(nodeId)}/receiving`,
    {
      method: 'PATCH',
      headers: mutationHeaders(csrf),
      body: JSON.stringify(body),
      signal,
    },
  )
}

export function patchGatewayReceiving(
  gatewayId: string,
  body: { receiving_enabled: boolean; expected_row_version: string },
  csrf: string,
  signal?: AbortSignal,
): Promise<GatewayReceivingPatchResponse> {
  return requestJson<GatewayReceivingPatchResponse>(
    `/api/v1/gateways/${encodeURIComponent(gatewayId)}/receiving`,
    {
      method: 'PATCH',
      headers: mutationHeaders(csrf),
      body: JSON.stringify(body),
      signal,
    },
  )
}
