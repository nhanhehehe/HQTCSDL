import { ApiError } from '../../api/client'
import {
  compareDecimalStrings,
  isValidExactDecimal92,
} from '../../api/monitoring'
import type {
  MetricThresholdPatchInput,
  MonitoringPatchPayload,
  MonitoringStateView,
} from '../../api/monitoring'
import type { MetricCode } from '../measurements/types'

export type MetricDef = {
  code: MetricCode
  unit: string
  min_value: string
  max_value: string
  decimal_places: number
}

export type MetricDraft = {
  metric_code: MetricCode
  enabled: boolean
  lower: string
  upper: string
}

export const DEFAULT_METRIC_BOUNDS: Record<MetricCode, { min: string; max: string; unit: string }> = {
  temperature: { min: '-40', max: '85', unit: '°C' },
  airHumidity: { min: '0', max: '100', unit: '%RH' },
  pressure: { min: '300', max: '1100', unit: 'hPa' },
  light: { min: '0', max: '65535', unit: 'lux' },
  soilMoisture: { min: '0', max: '100', unit: '%' },
}

export function createDraftFromMonitoring(mon: MonitoringStateView): {
  thresholds: Record<MetricCode, MetricDraft>
  timeoutMinutes: string
} {
  const thresholds = {} as Record<MetricCode, MetricDraft>
  for (const item of mon.thresholds) {
    thresholds[item.metric_code] = {
      metric_code: item.metric_code,
      enabled: item.enabled,
      lower: item.lower ?? '',
      upper: item.upper ?? '',
    }
  }
  return {
    thresholds,
    timeoutMinutes: mon.timeout_minutes !== null ? String(mon.timeout_minutes) : '',
  }
}

export function validateAndBuildThresholdPatch(
  drafts: Record<MetricCode, MetricDraft>,
  metricsCatalog: MetricDef[],
): { ok: true; thresholds: Partial<Record<MetricCode, MetricThresholdPatchInput>> } | { ok: false; error: string } {
  const catalogByCode = new Map(metricsCatalog.map(m => [m.code, m]))
  const out: Partial<Record<MetricCode, MetricThresholdPatchInput>> = {}

  for (const code of ['temperature', 'airHumidity', 'pressure', 'light', 'soilMoisture'] as MetricCode[]) {
    const item = drafts[code]
    const loRaw = item.lower.trim()
    const hiRaw = item.upper.trim()

    if (!loRaw && !hiRaw) {
      if (item.enabled) {
        return { ok: false, error: `Phải nhập đủ ngưỡng dưới và ngưỡng trên khi bật ${code}.` }
      }
      out[code] = { enabled: false }
      continue
    }

    if (!loRaw || !hiRaw) {
      return { ok: false, error: `Thiếu một đầu ngưỡng cho ${code} (không cho phép xóa một nửa cặp).` }
    }

    if (!isValidExactDecimal92(loRaw)) {
      return {
        ok: false,
        error: `Ngưỡng dưới tại thresholds.${code}.lower không hợp lệ theo DECIMAL(9,2).`,
      }
    }
    if (!isValidExactDecimal92(hiRaw)) {
      return {
        ok: false,
        error: `Ngưỡng trên tại thresholds.${code}.upper không hợp lệ theo DECIMAL(9,2).`,
      }
    }

    const cat = catalogByCode.get(code)
    const minStr = cat?.min_value ?? DEFAULT_METRIC_BOUNDS[code].min
    const maxStr = cat?.max_value ?? DEFAULT_METRIC_BOUNDS[code].max

    if (
      compareDecimalStrings(loRaw, minStr) < 0 ||
      compareDecimalStrings(loRaw, maxStr) > 0 ||
      compareDecimalStrings(hiRaw, minStr) < 0 ||
      compareDecimalStrings(hiRaw, maxStr) > 0
    ) {
      return {
        ok: false,
        error: `Ngưỡng của ${code} nằm ngoài miền kỹ thuật (${minStr}..${maxStr}).`,
      }
    }

    if (compareDecimalStrings(loRaw, hiRaw) >= 0) {
      return {
        ok: false,
        error: `Ngưỡng dưới phải nhỏ hơn ngưỡng trên cho ${code}.`,
      }
    }

    out[code] = {
      enabled: item.enabled,
      lower: loRaw,
      upper: hiRaw,
    }
  }

  return { ok: true, thresholds: out }
}

export function validateTimeoutMinutesInput(
  rawTimeout: string,
  mon: MonitoringStateView,
): { ok: true; timeoutMinutes: number } | { ok: false; error: string } {
  if (mon.timeout_validation.state === 'PROFILE_MISSING') {
    return {
      ok: false,
      error: 'PROFILE_MISSING: Node chưa có profile thời gian kỹ thuật nên không thể đổi timeout T.',
    }
  }
  const trimmed = rawTimeout.trim()
  if (!/^[1-9]\d*$/.test(trimmed)) {
    return { ok: false, error: 'timeout_minutes phải là số nguyên dương.' }
  }
  const parsed = Number(trimmed)
  if (!Number.isSafeInteger(parsed) || parsed > 2147483647) {
    return { ok: false, error: 'timeout_minutes vượt quá giới hạn số nguyên 32-bit.' }
  }
  const minT = mon.timeout_validation.min_timeout_minutes
  if (minT !== null && parsed < minT) {
    return {
      ok: false,
      error: `timeout_minutes tối thiểu theo profile là ${minT} phút.`,
    }
  }
  return { ok: true, timeoutMinutes: parsed }
}

export function buildAtomicMonitoringPatchPayload(opts: {
  includeThresholds: boolean
  includeTimeout: boolean
  mon: MonitoringStateView
  drafts: Record<MetricCode, MetricDraft>
  timeoutInput: string
  metricsCatalog: MetricDef[]
}): { ok: true; payload: MonitoringPatchPayload } | { ok: false; error: string } {
  const payload: MonitoringPatchPayload = {}
  if (opts.includeThresholds) {
    const thRes = validateAndBuildThresholdPatch(opts.drafts, opts.metricsCatalog)
    if (!thRes.ok) return thRes
    payload.expected_threshold_config_id = opts.mon.threshold_config_id
    payload.thresholds = thRes.thresholds
  }
  if (opts.includeTimeout) {
    const tRes = validateTimeoutMinutesInput(opts.timeoutInput, opts.mon)
    if (!tRes.ok) return tRes
    payload.expected_timeout_config_id = opts.mon.timeout_config_id
    payload.timeout_minutes = tRes.timeoutMinutes
  }
  return { ok: true, payload }
}

export function formatMonitoringApiError(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error && error.message ? error.message : fallback
  }
  // Per G4-D07: show reason + field path on 422 without echoing invalid raw values.
  if (error.status === 422 && error.details) {
    const reason = typeof error.details.reason === 'string' ? error.details.reason : 'THRESHOLD_DECIMAL_INVALID'
    const field = typeof error.details.field === 'string' ? error.details.field : ''
    return field ? `${reason} (${field}): ${error.message}` : `${reason}: ${error.message}`
  }
  if (error.status === 409 && error.code === 'CONFIG_VERSION_CONFLICT') {
    return 'Cấu hình đã thay đổi bởi thao tác khác (409 CONFIG_VERSION_CONFLICT). Vui lòng tải lại trước khi lưu.'
  }
  return error.code ? `${error.code}: ${error.message}` : error.message
}
