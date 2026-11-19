import { StatusBadge } from './StatusBadge'
import type { MetricValueView, SensorReadStatus } from '../features/measurements/types'

function readStatusLabel(status: SensorReadStatus): string {
  return status === 'INVALID_VALUE' ? 'Ngoài miền (INVALID_VALUE)' : 'Lỗi đọc (READ_ERROR)'
}

export function MetricCard({
  label,
  metric,
  fallbackUnit,
}: {
  label: string
  metric: MetricValueView | null
  fallbackUnit: string
}) {
  const unit = metric?.unit ?? fallbackUnit
  const isOk = metric !== null && metric.value !== null && metric.status === 'OK'

  return (
    <div className={`metric-card${metric && !isOk ? ' metric-card-err' : ''}`}>
      <div className="metric-card-head">
        <span className="metric-card-label">{label}</span>
        <span className="metric-card-unit">{unit}</span>
      </div>
      {metric === null ? (
        <div className="metric-card-empty muted">—</div>
      ) : isOk ? (
        <div className="metric-card-value tabular mono">
          {metric.value} <small>{unit}</small>
        </div>
      ) : (
        <div className="stack-sm">
          <StatusBadge tone="err" label="Lỗi cảm biến" />
          <small className="mono">{readStatusLabel(metric.status)}</small>
        </div>
      )}
    </div>
  )
}
