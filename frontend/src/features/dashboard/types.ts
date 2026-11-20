import type { CurrentMeasurementResponse } from '../measurements/types'

export type FreshnessState =
  | 'NO_DATA'
  | 'NO_EVENT_TIME'
  | 'CLOCK_AHEAD'
  | 'PROFILE_MISSING'
  | 'FRESH'
  | 'STALE'

export type FreshnessView = {
  state: FreshnessState
  age_seconds: string | null
  stale_after_seconds: number | null
}

export type DashboardCapabilities = {
  monitoring: boolean
  alerts: boolean
}

export type DashboardNodeSummary = {
  id: string
  device_id: string
  name: string
  zone: {
    id: string
    code: string
    name: string
    archived_at: string | null
  }
  gateway: {
    id: string
    gateway_id: string
    name: string
    receiving_enabled: boolean
  } | null
  receiving_enabled: boolean
}

export type NodeDashboardView = {
  profile: DashboardNodeSummary
  current: CurrentMeasurementResponse
  freshness: FreshnessView
  monitoring: null
  evaluation: null
  active_alerts: null
  active_count: string | null
}

export type DashboardView = {
  as_of: string
  capabilities: DashboardCapabilities
  scope: {
    kind: 'FARM' | 'NODE'
    farm_id: string
    farm: {
      id: string
      code: string
      name: string
      archived_at: string | null
    }
    node_ids: string[]
  }
  active_count: string | null
  nodes: NodeDashboardView[]
}
