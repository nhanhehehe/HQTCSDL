import type { AlertSummaryView } from '../../api/alerts'
import type { DashboardView, NodeDashboardView } from './types'

export function parseActiveCountValue(raw: string | number | null | undefined): number | null {
  if (raw === null || raw === undefined) return null
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : null
}

export function countFarmActiveAlerts(view: DashboardView): number | null {
  const rootCount = parseActiveCountValue(view.active_count)
  if (rootCount !== null) return rootCount
  let hasAnyNonNull = false
  let total = 0
  for (const node of view.nodes) {
    const val = parseActiveCountValue(node.active_count)
    if (val !== null) {
      hasAnyNonNull = true
      total += val
    } else if (Array.isArray(node.active_alerts)) {
      hasAnyNonNull = true
      total += node.active_alerts.length
    }
  }
  return hasAnyNonNull ? total : null
}

export function collectFarmActiveAlerts(view: DashboardView): AlertSummaryView[] {
  const byId = new Map<string, AlertSummaryView>()
  for (const node of view.nodes) {
    if (!Array.isArray(node.active_alerts)) continue
    for (const alert of node.active_alerts) {
      if (!byId.has(alert.id)) {
        byId.set(alert.id, alert)
      }
    }
  }
  const items = Array.from(byId.values())
  items.sort((a, b) => {
    if (a.lifecycle_status !== b.lifecycle_status) {
      return a.lifecycle_status === 'OPEN' ? -1 : 1
    }
    if (a.acknowledged !== b.acknowledged) {
      return a.acknowledged ? 1 : -1
    }
    const ta = new Date(a.first_detected_at).getTime()
    const tb = new Date(b.first_detected_at).getTime()
    return tb - ta
  })
  return items
}

export function summarizeFarmEvaluation(view: DashboardView): {
  enabled: boolean
  total: number
  upToDate: number
  pending: number
  error: number
} {
  let hasAny = false
  let total = 0
  let upToDate = 0
  let pending = 0
  let error = 0
  for (const node of view.nodes) {
    if (!node.evaluation) continue
    hasAny = true
    total += 1
    if (node.evaluation.status === 'UP_TO_DATE') upToDate += 1
    else if (node.evaluation.status === 'PENDING') pending += 1
    else if (node.evaluation.status === 'ERROR') error += 1
  }
  return {
    enabled: hasAny,
    total,
    upToDate,
    pending,
    error,
  }
}

export function summarizeNodeMonitoring(node: NodeDashboardView): {
  activeThresholdCount: number
  totalThresholdCount: number
  timeoutLabel: string
  timeoutTone: 'ok' | 'warn' | 'neutral'
} {
  const mon = node.monitoring
  if (!mon) {
    return {
      activeThresholdCount: 0,
      totalThresholdCount: 0,
      timeoutLabel: 'Chờ G4',
      timeoutTone: 'neutral',
    }
  }
  const activeThresholdCount = mon.thresholds.filter(t => t.state === 'ACTIVE').length
  const totalThresholdCount = mon.thresholds.length
  let timeoutLabel = 'Chưa cấu hình T'
  let timeoutTone: 'ok' | 'warn' | 'neutral' = 'neutral'
  if (mon.timeout_state === 'ACTIVE') {
    timeoutLabel = `Timeout ${mon.timeout_minutes ?? '?'}p · ACTIVE`
    timeoutTone = 'ok'
  } else if (mon.timeout_state === 'PAUSED') {
    timeoutLabel = `Timeout ${mon.timeout_minutes ?? '?'}p · PAUSED`
    timeoutTone = 'warn'
  }
  return {
    activeThresholdCount,
    totalThresholdCount,
    timeoutLabel,
    timeoutTone,
  }
}

export function findNewlyTriggeredAlerts(
  seenIds: Set<string>,
  currentAlerts: ReadonlyArray<AlertSummaryView>,
): AlertSummaryView[] {
  return currentAlerts.filter(a => a.lifecycle_status === 'OPEN' && !seenIds.has(a.id))
}
