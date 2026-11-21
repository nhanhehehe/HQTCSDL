import { METRIC_META, formatUtcToVi, sensorStatusLabel } from './format'
import type { ChartPointView, MetricCode, SensorReadStatus } from './types'

export const CHART_PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 200] as const
export type ChartPageSize = (typeof CHART_PAGE_SIZE_OPTIONS)[number]

export const DEFAULT_CHART_PAGE_SIZE: ChartPageSize = 50
export const CHART_BATCH_MAX_PAGES = 5

export const DEFAULT_SELECTED_METRICS: readonly MetricCode[] = ['temperature', 'airHumidity']

export const METRIC_CHART_COLORS: Record<MetricCode, string> = {
  temperature: '#c2410c',
  airHumidity: '#0369a1',
  pressure: '#6d28d9',
  light: '#b45309',
  soilMoisture: '#28573e',
}

export type ChartDatum = {
  value: [number, number | null]
  rawValue: string | null
  status: SensorReadStatus
  unit: string
  deviceId: string
  bootId: string
  seq: string
  eventTime: string
  pointIndex: number
  isIsolated: boolean
}

export type MetricSeriesModel = {
  code: MetricCode
  label: string
  unit: string
  color: string
  data: ChartDatum[]
  validCount: number
  errorCount: number
  minValue: string | null
  maxValue: string | null
}

export type LoadedTimeSpan = {
  firstMs: number
  lastMs: number
  firstIso: string
  lastIso: string
  spanMs: number
}

const VI_OFFSET_MS = 7 * 60 * 60 * 1000

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

export function escapeHtml(raw: string): string {
  return raw
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

export function computeLoadedTimeSpan(points: readonly ChartPointView[]): LoadedTimeSpan | null {
  if (points.length === 0) return null
  const first = points[0]
  const last = points[points.length - 1]
  const firstMs = Date.parse(first.event_time)
  const lastMs = Date.parse(last.event_time)
  if (!Number.isFinite(firstMs) || !Number.isFinite(lastMs)) return null
  return {
    firstMs,
    lastMs,
    firstIso: first.event_time,
    lastIso: last.event_time,
    spanMs: Math.max(0, lastMs - firstMs),
  }
}

export function formatChartAxisTimeVi(utcMs: number, spanMs: number): string {
  if (!Number.isFinite(utcMs)) return ''
  const vi = new Date(utcMs + VI_OFFSET_MS)
  const hhmm = `${pad2(vi.getUTCHours())}:${pad2(vi.getUTCMinutes())}`
  if (spanMs <= 10 * 60 * 1000) {
    return `${hhmm}:${pad2(vi.getUTCSeconds())}`
  }
  if (spanMs <= 20 * 60 * 60 * 1000) {
    return hhmm
  }
  return `${pad2(vi.getUTCDate())}/${pad2(vi.getUTCMonth() + 1)}\n${hhmm}`
}

export function buildMetricSeries(
  points: readonly ChartPointView[],
  code: MetricCode,
): MetricSeriesModel {
  const meta = METRIC_META.find(m => m.code === code) ?? {
    code,
    label: code,
    unit: '',
  }
  const resolvedUnit = points[0]?.measurements[code]?.unit || meta.unit

  const numericValues: Array<number | null> = new Array(points.length)
  let validCount = 0
  let errorCount = 0
  let minNum: number | null = null
  let maxNum: number | null = null
  let minValue: string | null = null
  let maxValue: string | null = null

  const rawDrafts: Array<Omit<ChartDatum, 'isIsolated'>> = []

  for (let idx = 0; idx < points.length; idx += 1) {
    const pt = points[idx]
    const metric = pt.measurements[code]
    const utcMs = Date.parse(pt.event_time)
    let y: number | null = null
    if (metric.status === 'OK' && metric.value !== null) {
      const parsed = Number(metric.value)
      if (Number.isFinite(parsed)) {
        y = parsed
      }
    }

    numericValues[idx] = y
    if (y !== null) {
      validCount += 1
      if (minNum === null || y < minNum) {
        minNum = y
        minValue = metric.value
      }
      if (maxNum === null || y > maxNum) {
        maxNum = y
        maxValue = metric.value
      }
    } else {
      errorCount += 1
    }

    rawDrafts.push({
      value: [Number.isFinite(utcMs) ? utcMs : idx, y],
      rawValue: metric.value,
      status: metric.status,
      unit: metric.unit || resolvedUnit,
      deviceId: pt.identity.device_id,
      bootId: pt.identity.boot_id,
      seq: pt.identity.seq,
      eventTime: pt.event_time,
      pointIndex: idx,
    })
  }

  const data: ChartDatum[] = rawDrafts.map((draft, idx) => {
    const y = numericValues[idx]
    const prevNull = idx === 0 || numericValues[idx - 1] === null
    const nextNull = idx === numericValues.length - 1 || numericValues[idx + 1] === null
    return {
      ...draft,
      isIsolated: y !== null && prevNull && nextNull,
    }
  })

  return {
    code,
    label: meta.label,
    unit: resolvedUnit,
    color: METRIC_CHART_COLORS[code],
    data,
    validCount,
    errorCount,
    minValue,
    maxValue,
  }
}

export function formatMetricTooltipHtml(
  series: Pick<MetricSeriesModel, 'label' | 'color' | 'unit'>,
  items: readonly ChartDatum[],
): string {
  if (items.length === 0) return ''
  const headerTime = escapeHtml(formatUtcToVi(items[0].eventTime))
  const rows = items.map(item => {
    const identity = `Boot ${escapeHtml(item.bootId)} / Seq ${escapeHtml(item.seq)}`
    if (item.status === 'OK' && item.rawValue !== null) {
      return (
        `<div style="margin-top:4px;display:flex;justify-content:space-between;gap:12px;">` +
        `<span style="color:#53665c;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;">${identity}</span>` +
        `<strong style="color:#18362b;font-family:ui-monospace,Menlo,Consolas,monospace;">${escapeHtml(item.rawValue)} ${escapeHtml(item.unit || series.unit)}</strong>` +
        `</div>`
      )
    }
    return (
      `<div style="margin-top:4px;display:flex;justify-content:space-between;gap:12px;">` +
      `<span style="color:#53665c;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;">${identity}</span>` +
      `<strong style="color:#a61b1b;">${escapeHtml(sensorStatusLabel(item.status))}</strong>` +
      `</div>`
    )
  })

  return (
    `<div style="min-width:200px;font-size:12px;line-height:1.4;">` +
    `<div style="font-weight:700;color:#18362b;margin-bottom:2px;">` +
    `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${escapeHtml(series.color)};margin-right:6px;"></span>` +
    `${escapeHtml(series.label)}` +
    `</div>` +
    `<div style="color:#53665c;font-size:11px;margin-bottom:4px;">${headerTime}</div>` +
    rows.join('') +
    `</div>`
  )
}
