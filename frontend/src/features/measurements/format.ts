import type { BadgeTone } from '../../components/StatusBadge'
import type { FreshnessState } from '../dashboard/types'
import type { MetricCode, SensorReadStatus } from './types'

export const METRIC_META: ReadonlyArray<{ code: MetricCode; label: string; unit: string }> = [
  { code: 'temperature', label: 'Nhiệt độ', unit: '°C' },
  { code: 'airHumidity', label: 'Độ ẩm KK', unit: '%RH' },
  { code: 'pressure', label: 'Áp suất', unit: 'hPa' },
  { code: 'light', label: 'Ánh sáng', unit: 'lux' },
  { code: 'soilMoisture', label: 'Độ ẩm đất', unit: '%' },
]

const VI_OFFSET_MS = 7 * 60 * 60 * 1000

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

export function formatUtcMsToVi(utcMs: number): string {
  if (!Number.isFinite(utcMs)) return '—'
  const vi = new Date(utcMs + VI_OFFSET_MS)
  const datePart = `${pad2(vi.getUTCDate())}/${pad2(vi.getUTCMonth() + 1)}/${vi.getUTCFullYear()}`
  const timePart = `${pad2(vi.getUTCHours())}:${pad2(vi.getUTCMinutes())}:${pad2(vi.getUTCSeconds())}`
  return `${timePart} ${datePart} (UTC+07:00)`
}

export function formatUtcToVi(iso: string | null): string {
  if (!iso) return '—'
  const parsed = Date.parse(iso)
  if (!Number.isFinite(parsed)) return '—'
  return formatUtcMsToVi(parsed)
}

export function formatEpochToVi(epochStr: string | null): string {
  if (!epochStr || !/^-?[0-9]+$/.test(epochStr)) return '—'
  const sec = Number(epochStr)
  if (!Number.isSafeInteger(sec)) return `${epochStr}s`
  return formatUtcMsToVi(sec * 1000)
}

export function utcIsoToViDatetimeInput(iso: string): string {
  const parsed = Date.parse(iso)
  if (!Number.isFinite(parsed)) return ''
  const vi = new Date(parsed + VI_OFFSET_MS)
  return (
    `${vi.getUTCFullYear()}-${pad2(vi.getUTCMonth() + 1)}-${pad2(vi.getUTCDate())}` +
    `T${pad2(vi.getUTCHours())}:${pad2(vi.getUTCMinutes())}`
  )
}

export function defaultViRangeInputs(nowMs = Date.now()): { fromInput: string; toInput: string } {
  const toIso = new Date(nowMs).toISOString()
  const fromIso = new Date(nowMs - 24 * 60 * 60 * 1000).toISOString()
  return {
    fromInput: utcIsoToViDatetimeInput(fromIso),
    toInput: utcIsoToViDatetimeInput(toIso),
  }
}

export function viDatetimeInputToIso(input: string): string {
  const trimmed = input.trim()
  if (!trimmed) return ''
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(trimmed)) {
    return `${trimmed}:00+07:00`
  }
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(trimmed)) {
    return `${trimmed}+07:00`
  }
  throw new Error('Định dạng thời gian không hợp lệ (cần YYYY-MM-DDTHH:mm)')
}

export function freshnessBadge(state: FreshnessState): { tone: BadgeTone; label: string } {
  switch (state) {
    case 'FRESH':
      return { tone: 'ok', label: 'Mới' }
    case 'STALE':
      return { tone: 'warn', label: 'Quá hạn chu kỳ hiển thị' }
    case 'CLOCK_AHEAD':
      return { tone: 'warn', label: 'Giờ mẫu vượt giờ Backend' }
    case 'PROFILE_MISSING':
      return { tone: 'neutral', label: 'Thiếu profile thời gian' }
    case 'NO_EVENT_TIME':
      return { tone: 'neutral', label: 'Chưa có giờ đo' }
    case 'NO_DATA':
    default:
      return { tone: 'neutral', label: 'Chưa có dữ liệu' }
  }
}

export function sensorStatusLabel(status: SensorReadStatus): string {
  return status === 'INVALID_VALUE' ? 'Ngoài miền (INVALID_VALUE)' : 'Lỗi cảm biến'
}

export function receivingBadge(enabled: boolean): { tone: BadgeTone; label: string } {
  return enabled
    ? { tone: 'ok', label: 'Đang nhận dữ liệu mới' }
    : { tone: 'neutral', label: 'Đã tắt tiếp nhận mới' }
}

export function areaLifecycleBadge(archivedAt: string | null | undefined): {
  tone: BadgeTone
  label: string
} {
  return archivedAt
    ? { tone: 'neutral', label: 'Đã lưu trữ' }
    : { tone: 'ok', label: 'Đang hoạt động' }
}
