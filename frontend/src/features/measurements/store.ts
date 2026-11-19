import { StaleResponse, getJson } from '../../api/client'
import { defaultViRangeInputs, viDatetimeInputToIso } from './format'
import { describeApiError, isAbortOrStale } from './scopeLoader'
import type {
  CurrentMeasurementResponse,
  HistoryState,
  MeasurementHistoryResponse,
  MeasurementView,
} from './types'

export type TimeMode = 'known' | 'unknown'
export type HistoryViewTab = 'table' | 'chart'

export type MeasurementsStoreState = {
  nodeId: string
  current: {
    loading: boolean
    error: string
    data: CurrentMeasurementResponse | null
    showingPrevious: boolean
  }
  history: {
    timeMode: TimeMode
    viewTab: HistoryViewTab
    fromInput: string
    toInput: string
    loading: boolean
    loadingMore: boolean
    error: string
    state: HistoryState | null
    items: MeasurementView[]
    nextCursor: string | null
    viewToken: string | null
    range: { from: string; to: string } | null
    asOf: string | null
    showingPrevious: boolean
  }
}

export class MeasurementsStore {
  private state: MeasurementsStoreState
  private listeners = new Set<() => void>()
  private currentGen = 0
  private historyGen = 0
  private currentCtrl: AbortController | null = null
  private historyCtrl: AbortController | null = null

  constructor(nowMs?: number) {
    const defaults = defaultViRangeInputs(nowMs)
    this.state = {
      nodeId: '',
      current: {
        loading: false,
        error: '',
        data: null,
        showingPrevious: false,
      },
      history: {
        timeMode: 'known',
        viewTab: 'table',
        fromInput: defaults.fromInput,
        toInput: defaults.toInput,
        loading: false,
        loadingMore: false,
        error: '',
        state: null,
        items: [],
        nextCursor: null,
        viewToken: null,
        range: null,
        asOf: null,
        showingPrevious: false,
      },
    }
  }

  getSnapshot = () => this.state

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private emit(patch: Partial<MeasurementsStoreState>) {
    this.state = { ...this.state, ...patch }
    for (const listener of this.listeners) listener()
  }

  setNodeId(nextNodeId: string, options: { timeMode?: TimeMode } = {}) {
    this.currentGen += 1
    this.historyGen += 1
    this.currentCtrl?.abort()
    this.historyCtrl?.abort()
    this.currentCtrl = null
    this.historyCtrl = null
    const timeMode = options.timeMode ?? this.state.history.timeMode
    this.emit({
      nodeId: nextNodeId,
      current: {
        loading: false,
        error: '',
        data: null,
        showingPrevious: false,
      },
      history: {
        ...this.state.history,
        timeMode,
        viewTab: timeMode === 'unknown' ? 'table' : this.state.history.viewTab,
        loading: false,
        loadingMore: false,
        error: '',
        state: null,
        items: [],
        nextCursor: null,
        viewToken: null,
        range: null,
        asOf: null,
        showingPrevious: false,
      },
    })
  }

  setTimeMode(mode: TimeMode) {
    this.historyGen += 1
    this.historyCtrl?.abort()
    this.historyCtrl = null
    this.emit({
      history: {
        ...this.state.history,
        timeMode: mode,
        viewTab: mode === 'unknown' ? 'table' : this.state.history.viewTab,
        loading: false,
        loadingMore: false,
        error: '',
        state: null,
        items: [],
        nextCursor: null,
        viewToken: null,
        range: null,
        asOf: null,
        showingPrevious: false,
      },
    })
  }

  setViewTab(tab: HistoryViewTab) {
    if (this.state.history.timeMode === 'unknown' && tab === 'chart') return
    this.emit({ history: { ...this.state.history, viewTab: tab } })
  }

  setFilterInputs(fromInput: string, toInput: string) {
    this.emit({
      history: {
        ...this.state.history,
        fromInput,
        toInput,
      },
    })
  }

  resetDefaultFilter(nowMs?: number) {
    const defaults = defaultViRangeInputs(nowMs)
    this.historyGen += 1
    this.historyCtrl?.abort()
    this.historyCtrl = null
    this.emit({
      history: {
        ...this.state.history,
        fromInput: defaults.fromInput,
        toInput: defaults.toInput,
        nextCursor: null,
        viewToken: null,
      },
    })
  }

  async loadCurrent(): Promise<void> {
    const nodeId = this.state.nodeId
    if (!nodeId) return
    this.currentCtrl?.abort()
    const controller = new AbortController()
    this.currentCtrl = controller
    const expectedGen = ++this.currentGen
    this.emit({
      current: {
        ...this.state.current,
        loading: true,
        error: '',
        showingPrevious: false,
      },
    })
    try {
      const data = await getJson<CurrentMeasurementResponse>(
        `/api/v1/nodes/${encodeURIComponent(nodeId)}/measurements/current`,
        controller.signal,
      )
      if (controller.signal.aborted || expectedGen !== this.currentGen) return
      this.emit({
        current: {
          loading: false,
          error: '',
          data,
          showingPrevious: false,
        },
      })
    } catch (error) {
      if (error instanceof StaleResponse || isAbortOrStale(error)) return
      if (expectedGen !== this.currentGen) return
      this.emit({
        current: {
          ...this.state.current,
          loading: false,
          error: describeApiError(error, 'Không tải được dữ liệu hiện tại.'),
          showingPrevious: this.state.current.data !== null,
        },
      })
    }
  }

  async loadHistory(loadMore = false): Promise<void> {
    const nodeId = this.state.nodeId
    if (!nodeId) return
    const h = this.state.history
    if (loadMore && (!h.nextCursor || !h.viewToken)) return

    this.historyCtrl?.abort()
    const controller = new AbortController()
    this.historyCtrl = controller
    const expectedGen = ++this.historyGen

    const params = new URLSearchParams({
      time_mode: h.timeMode,
      page_size: '50',
    })
    if (loadMore && h.nextCursor && h.viewToken) {
      params.set('cursor', h.nextCursor)
      params.set('view_token', h.viewToken)
    } else if (h.timeMode === 'known') {
      try {
        const fromIso = viDatetimeInputToIso(h.fromInput)
        const toIso = viDatetimeInputToIso(h.toInput)
        if (fromIso) params.set('from', fromIso)
        if (toIso) params.set('to', toIso)
      } catch (error) {
        this.emit({
          history: {
            ...this.state.history,
            loading: false,
            loadingMore: false,
            error: describeApiError(error, 'Định dạng thời gian không hợp lệ.'),
          },
        })
        return
      }
    }

    this.emit({
      history: {
        ...this.state.history,
        loading: !loadMore,
        loadingMore: loadMore,
        error: '',
        showingPrevious: false,
      },
    })

    try {
      const page = await getJson<MeasurementHistoryResponse>(
        `/api/v1/nodes/${encodeURIComponent(nodeId)}/measurements?${params}`,
        controller.signal,
      )
      if (controller.signal.aborted || expectedGen !== this.historyGen) return
      const nextItems = loadMore ? [...this.state.history.items, ...page.items] : page.items
      this.emit({
        history: {
          ...this.state.history,
          loading: false,
          loadingMore: false,
          error: '',
          state: page.state,
          items: nextItems,
          nextCursor: page.next_cursor,
          viewToken: page.view_token,
          range: page.range,
          asOf: page.as_of,
          showingPrevious: false,
        },
      })
    } catch (error) {
      if (error instanceof StaleResponse || isAbortOrStale(error)) return
      if (expectedGen !== this.historyGen) return
      this.emit({
        history: {
          ...this.state.history,
          loading: false,
          loadingMore: false,
          error: describeApiError(error, 'Không tải được lịch sử đo.'),
          showingPrevious: this.state.history.state !== null,
        },
      })
    }
  }

  dispose() {
    this.currentGen += 1
    this.historyGen += 1
    this.currentCtrl?.abort()
    this.historyCtrl?.abort()
    this.currentCtrl = null
    this.historyCtrl = null
    this.listeners.clear()
  }
}
