import { ApiError, StaleResponse, getJson } from '../../api/client'
import { describeApiError } from '../measurements/scopeLoader'
import type { DashboardView } from './types'

export const DASHBOARD_POLL_INTERVAL_MS = 5000
export const DASHBOARD_REQUEST_TIMEOUT_MS = 4000

export type DashboardCoordinatorState = {
  farmId: string
  selectedNodeId: string
  snapshot: DashboardView | null
  loading: boolean
  refreshing: boolean
  showingStale: boolean
  lastSuccessAt: string | null
  error: string
  dataEpoch: number
}

export type DashboardTimerDeps = {
  now?: () => number
  setTimer?: (fn: () => void, delayMs: number) => ReturnType<typeof setTimeout>
  clearTimer?: (handle: ReturnType<typeof setTimeout>) => void
  isHidden?: () => boolean
}

export class DashboardCoordinator {
  private state: DashboardCoordinatorState = {
    farmId: '',
    selectedNodeId: '',
    snapshot: null,
    loading: false,
    refreshing: false,
    showingStale: false,
    lastSuccessAt: null,
    error: '',
    dataEpoch: 0,
  }

  private listeners = new Set<() => void>()
  private readonly now: () => number
  private readonly setTimer: (fn: () => void, delayMs: number) => ReturnType<typeof setTimeout>
  private readonly clearTimer: (handle: ReturnType<typeof setTimeout>) => void
  private readonly isHidden: () => boolean

  private timer: ReturnType<typeof setTimeout> | undefined
  private inFlightCtrl: AbortController | null = null
  private requestSeq = 0
  private lastStartedAt: number | null = null
  private paused = false
  private stopped = false
  private pendingImmediate = false

  constructor(deps: DashboardTimerDeps = {}) {
    this.now = deps.now ?? (() => performance.now())
    this.setTimer = deps.setTimer ?? ((fn, delayMs) => setTimeout(fn, delayMs))
    this.clearTimer = deps.clearTimer ?? (handle => clearTimeout(handle))
    this.isHidden =
      deps.isHidden ??
      (() => (typeof document !== 'undefined' ? Boolean(document.hidden) : false))
  }

  getSnapshot = () => this.state

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private emit(patch: Partial<DashboardCoordinatorState>) {
    this.state = { ...this.state, ...patch }
    for (const listener of this.listeners) listener()
  }

  private cancelTimer() {
    if (this.timer !== undefined) {
      this.clearTimer(this.timer)
      this.timer = undefined
    }
  }

  private scheduleNext(delayMs: number) {
    this.cancelTimer()
    if (this.stopped || this.paused || !this.state.farmId) return
    this.timer = this.setTimer(() => {
      this.timer = undefined
      if (this.stopped || this.paused || !this.state.farmId) return
      if (this.inFlightCtrl !== null) {
        // Single-flight: skip tick while a fetch is still pending.
        return
      }
      void this.fetchOnce()
    }, Math.max(0, delayMs))
  }

  setFarm(farmId: string) {
    this.cancelTimer()
    this.inFlightCtrl?.abort()
    this.inFlightCtrl = null
    this.lastStartedAt = null
    this.pendingImmediate = false
    this.stopped = !farmId
    this.paused = this.isHidden()

    this.emit({
      farmId,
      selectedNodeId: '',
      snapshot: null,
      loading: Boolean(farmId),
      refreshing: false,
      showingStale: false,
      lastSuccessAt: null,
      error: '',
      dataEpoch: this.state.dataEpoch + 1,
    })

    if (farmId && !this.paused) {
      void this.fetchOnce()
    }
  }

  selectNode(nodeId: string) {
    if (nodeId === this.state.selectedNodeId) return
    this.emit({ selectedNodeId: nodeId })
  }

  refreshNow() {
    if (!this.state.farmId || this.stopped) return
    this.cancelTimer()
    this.inFlightCtrl?.abort()
    this.inFlightCtrl = null
    this.pendingImmediate = false
    this.emit({
      dataEpoch: this.state.dataEpoch + 1,
      refreshing: this.state.snapshot !== null,
      loading: this.state.snapshot === null,
    })
    void this.fetchOnce()
  }

  setVisibility(hidden: boolean) {
    this.paused = hidden
    if (hidden) {
      this.cancelTimer()
      return
    }
    if (!this.state.farmId || this.stopped) return
    if (this.inFlightCtrl !== null) {
      this.pendingImmediate = true
      return
    }
    this.cancelTimer()
    void this.fetchOnce()
  }

  handleResume() {
    if (this.paused || !this.state.farmId || this.stopped) return
    if (this.inFlightCtrl !== null) {
      this.pendingImmediate = true
      return
    }
    this.cancelTimer()
    void this.fetchOnce()
  }

  async fetchOnce(): Promise<void> {
    const expectedFarm = this.state.farmId
    if (!expectedFarm || this.stopped) return
    if (this.inFlightCtrl !== null) return

    this.cancelTimer()
    const controller = new AbortController()
    this.inFlightCtrl = controller
    const expectedEpoch = this.state.dataEpoch
    const seq = ++this.requestSeq
    const startedAt = this.now()
    this.lastStartedAt = startedAt

    this.emit({
      loading: this.state.snapshot === null,
      refreshing: this.state.snapshot !== null,
    })

    try {
      const data = await getJson<DashboardView>(
        `/api/v1/dashboard?farm_id=${encodeURIComponent(expectedFarm)}`,
        controller.signal,
        DASHBOARD_REQUEST_TIMEOUT_MS,
      )
      if (
        this.stopped ||
        controller.signal.aborted ||
        expectedFarm !== this.state.farmId ||
        expectedEpoch !== this.state.dataEpoch ||
        seq !== this.requestSeq
      ) {
        return
      }
      const validIds = new Set(data.nodes.map(n => n.profile.id))
      const currentSelected = this.state.selectedNodeId
      const nextSelected =
        currentSelected && validIds.has(currentSelected)
          ? currentSelected
          : (data.nodes[0]?.profile.id ?? '')
      this.emit({
        snapshot: data,
        selectedNodeId: nextSelected,
        loading: false,
        refreshing: false,
        showingStale: false,
        lastSuccessAt: data.as_of,
        error: '',
      })
    } catch (error) {
      if (error instanceof StaleResponse || (error instanceof ApiError && error.status === 401)) {
        // 401 / auth invalidation has higher priority than stale cache retention.
        this.stopped = true
        this.cancelTimer()
        this.emit({
          snapshot: null,
          selectedNodeId: '',
          loading: false,
          refreshing: false,
          showingStale: false,
          lastSuccessAt: null,
          error: error instanceof ApiError ? error.message : '',
        })
        return
      }
      if (
        this.stopped ||
        expectedFarm !== this.state.farmId ||
        expectedEpoch !== this.state.dataEpoch ||
        seq !== this.requestSeq
      ) {
        return
      }
      const msg =
        (error instanceof DOMException && error.name === 'AbortError') ||
        (error instanceof Error && error.name === 'AbortError')
          ? 'Hết thời gian chờ phản hồi Dashboard (4 giây).'
          : describeApiError(error, 'Không thể kết nối tới Backend.')
      this.emit({
        loading: false,
        refreshing: false,
        showingStale: this.state.snapshot !== null,
        error: msg,
      })
    } finally {
      if (this.inFlightCtrl === controller) {
        this.inFlightCtrl = null
        if (!this.stopped && !this.paused && expectedFarm === this.state.farmId) {
          const elapsed = this.now() - startedAt
          if (this.pendingImmediate || elapsed >= DASHBOARD_POLL_INTERVAL_MS) {
            this.pendingImmediate = false
            this.scheduleNext(0)
          } else {
            this.scheduleNext(DASHBOARD_POLL_INTERVAL_MS - elapsed)
          }
        }
      }
    }
  }

  dispose() {
    this.stopped = true
    this.cancelTimer()
    this.inFlightCtrl?.abort()
    this.inFlightCtrl = null
    this.listeners.clear()
  }
}
