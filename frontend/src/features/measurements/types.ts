export type MetricCode = 'temperature' | 'airHumidity' | 'pressure' | 'light' | 'soilMoisture'

export type SensorReadStatus = 'OK' | 'READ_ERROR' | 'INVALID_VALUE'

export type MetricValueView = {
  value: string | null
  status: SensorReadStatus
  unit: string
}

export type MeasurementIdentityView = {
  device_id: string
  boot_id: string
  seq: string
}

export type MeasurementView = {
  id: string
  node_id: string
  identity: MeasurementIdentityView
  t_node: string | null
  event_time: string | null
  time_status: 'VALID' | 'UNKNOWN'
  measurements: Record<MetricCode, MetricValueView>
  gateway_received_at_epoch: string
  persisted_at: string
}

export type CurrentMeasurementState = 'NO_DATA' | 'NO_EVENT_TIME' | 'AVAILABLE'

export type CurrentMeasurementResponse = {
  state: CurrentMeasurementState
  measurement: MeasurementView | null
  as_of: string
}

export type HistoryState = 'NO_DATA' | 'NO_EVENT_TIME' | 'EMPTY_RANGE' | 'AVAILABLE'

export type MeasurementHistoryResponse = {
  state: HistoryState
  items: MeasurementView[]
  next_cursor: string | null
  view_token: string
  range: { from: string; to: string } | null
  as_of: string
}

export type ChartPointView = {
  identity: MeasurementIdentityView
  event_time: string
  measurements: Record<MetricCode, MetricValueView>
}

export type MeasurementChartResponse = {
  state: HistoryState
  points: ChartPointView[]
  next_cursor: string | null
  view_token: string
  range: { from: string; to: string } | null
  as_of: string
}

export type NodeChoice = {
  id: string
  device_id: string
  name: string
  zone: {
    id: string
    code: string
    name: string
    archived_at?: string | null
  }
  receiving_enabled: boolean
}
