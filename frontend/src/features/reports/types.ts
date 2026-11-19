import type { MetricCode } from '../measurements/types'

export type MetricStatsView = {
  metric_code: MetricCode
  unit: string
  n_valid: string
  n_invalid: string
  min: string | null
  max: string | null
  avg: string | null
}

export type ReportContextView = {
  source: string
  farm: { id: string; code: string; name: string }
  zone: { id: string; code: string; name: string }
  node: { id: string; device_id: string; name: string }
  from: string
  to: string
  timezone: 'UTC+07:00'
}

export type ReportView = {
  context: ReportContextView
  as_of: string
  N_total: string
  rows: MetricStatsView[]
  snapshot_token: string
  expires_at: string
}
