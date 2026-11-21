import { ApiError, StaleResponse, getJson } from '../../api/client'
import { CHART_BATCH_MAX_PAGES, DEFAULT_CHART_PAGE_SIZE } from './chartData'
import type { ChartPageSize } from './chartData'
import { viDatetimeInputToIso } from './format'
import { describeApiError, isAbortOrStale } from './scopeLoader'
import type {
  ChartPointView,
  CurrentMeasurementResponse,
  HistoryState,
  MeasurementChartResponse,
  MeasurementHistoryResponse,
  MeasurementView,
} from './types'

export type TimeMode = 'known' | 'unknown'
export type HistoryViewTab = 'table' | 'chart'

export type ChartSliceState = {
  pageSize: ChartPageSize
  loading: boolean
  loadingMore: boolean
  loadingBatch: boolean
  error: string
  errorCode: string | null
  state: HistoryState | null
  points: ChartPointView[]
  nextCursor: string | null
  viewToken: string | null
  showingPrevious: boolean
}

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
  chart: ChartSliceState
}

function emptyChartSlice(pageSize: ChartPageSize): ChartSliceState {
  return {
    pageSize,
    loading: false,
    loadingMore: false,
    loadingBatch: false,
    error: '',
    errorCode: null,
    state: null,
    points: [],
    nextCursor: null,
    viewToken: null,
    showingPrevious: false,
  }
}

export class MeasurementsStore {
  private state: MeasurementsStoreState
  private listeners = new Set<() => void>()
  private currentGen = 0
  private historyGen = 0
  private chartGen = 0
  private currentCtrl: AbortController | null = null
  private historyCtrl: AbortController | null = null
  private chartCtrl: AbortController | null = null

  constructor() {
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
        fromInput: '',
        toInput: '',
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
      chart: emptyChartSlice(DEFAULT_CHART_PAGE_SIZE),
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

  private abortChart() {
    this.chartGen += 1
    this.chartCtrl?.abort()
    this.chartCtrl = null
  }

  setNodeId(nextNodeId: string, options: { timeMode?: TimeMode; viewTab?: HistoryViewTab } = {}) {
    this.currentGen += 1
    this.historyGen += 1
    this.currentCtrl?.abort()
    this.historyCtrl?.abort()
    this.currentCtrl = null
    this.historyCtrl = null
    this.abortChart()

    const timeMode = options.timeMode ?? this.state.history.timeMode
    const requestedTab = options.viewTab ?? this.state.history.viewTab
    const viewTab: HistoryViewTab = timeMode === 'unknown' ? 'table' : requestedTab

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
        viewTab,
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
      chart: emptyChartSlice(this.state.chart.pageSize),
    })
  }

  setTimeMode(mode: TimeMode) {
    this.historyGen += 1
    this.historyCtrl?.abort()
    this.historyCtrl = null
    this.abortChart()
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
      chart: emptyChartSlice(this.state.chart.pageSize),
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

  setChartPageSize(pageSize: ChartPageSize) {
    if (this.state.chart.pageSize === pageSize) return
    this.abortChart()
    this.emit({
      chart: emptyChartSlice(pageSize),
    })
  }

  resetDefaultFilter() {
    this.historyGen += 1
    this.historyCtrl?.abort()
    this.historyCtrl = null
    this.abortChart()
    this.emit({
      history: {
        ...this.state.history,
        fromInput: '',
        toInput: '',
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
      chart: emptyChartSlice(this.state.chart.pageSize),
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

  async loadHistory(
    loadMore = false,
    options: { reuseViewToken?: boolean } = {},
  ): Promise<void> {
    const nodeId = this.state.nodeId
    if (!nodeId) return
    const h = this.state.history
    if (loadMore && (!h.nextCursor || !h.viewToken)) return

    const reuseSharedToken =
      !loadMore && Boolean(options.reuseViewToken) && h.timeMode === 'known' && Boolean(h.viewToken)

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
    } else if (reuseSharedToken && h.viewToken) {
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

    if (!loadMore && !reuseSharedToken) {
      this.abortChart()
    }

    this.emit({
      history: {
        ...this.state.history,
        loading: !loadMore,
        loadingMore: loadMore,
        error: '',
        showingPrevious: false,
      },
      ...(!loadMore && !reuseSharedToken
        ? { chart: emptyChartSlice(this.state.chart.pageSize) }
        : {}),
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

  async loadChart(
    loadMore = false,
    options: { freshSnapshot?: boolean } = {},
  ): Promise<void> {
    const nodeId = this.state.nodeId
    if (!nodeId || this.state.history.timeMode !== 'known') return
    const c = this.state.chart
    if (loadMore && (!c.nextCursor || !c.viewToken)) return

    const reuseSharedToken =
      !loadMore && !options.freshSnapshot && Boolean(this.state.history.viewToken)

    this.chartCtrl?.abort()
    const controller = new AbortController()
    this.chartCtrl = controller
    const expectedGen = ++this.chartGen

    const params = new URLSearchParams({
      page_size: String(c.pageSize),
    })

    if (loadMore && c.nextCursor && c.viewToken) {
      params.set('cursor', c.nextCursor)
      params.set('view_token', c.viewToken)
    } else if (reuseSharedToken && this.state.history.viewToken) {
      params.set('view_token', this.state.history.viewToken)
    } else {
      try {
        const fromIso = viDatetimeInputToIso(this.state.history.fromInput)
        const toIso = viDatetimeInputToIso(this.state.history.toInput)
        if (fromIso) params.set('from', fromIso)
        if (toIso) params.set('to', toIso)
      } catch (error) {
        this.emit({
          chart: {
            ...this.state.chart,
            loading: false,
            loadingMore: false,
            loadingBatch: false,
            error: describeApiError(error, 'Định dạng thời gian không hợp lệ.'),
            errorCode: 'RANGE_INVALID',
          },
        })
        return
      }
    }

    if (!loadMore && !reuseSharedToken) {
      this.historyGen += 1
      this.historyCtrl?.abort()
      this.historyCtrl = null
    }

    this.emit({
      ...(!loadMore && !reuseSharedToken
        ? {
            history: {
              ...this.state.history,
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
        : {}),
      chart: {
        ...this.state.chart,
        loading: !loadMore,
        loadingMore: loadMore,
        loadingBatch: false,
        error: '',
        errorCode: null,
        showingPrevious: false,
      },
    })

    try {
      const page = await getJson<MeasurementChartResponse>(
        `/api/v1/nodes/${encodeURIComponent(nodeId)}/measurements/chart?${params}`,
        controller.signal,
      )
      if (controller.signal.aborted || expectedGen !== this.chartGen) return
      const nextPoints = loadMore ? [...this.state.chart.points, ...page.points] : page.points
      this.emit({
        history: {
          ...this.state.history,
          viewToken: page.view_token,
          range: page.range,
          asOf: page.as_of,
        },
        chart: {
          ...this.state.chart,
          loading: false,
          loadingMore: false,
          loadingBatch: false,
          error: '',
          errorCode: null,
          state: page.state,
          points: nextPoints,
          nextCursor: page.next_cursor,
          viewToken: page.view_token,
          showingPrevious: false,
        },
      })
    } catch (error) {
      if (error instanceof StaleResponse || isAbortOrStale(error)) return
      if (expectedGen !== this.chartGen) return
      const errorCode = error instanceof ApiError ? error.code : null
      this.emit({
        chart: {
          ...this.state.chart,
          loading: false,
          loadingMore: false,
          loadingBatch: false,
          error: describeApiError(error, 'Không tải được dữ liệu biểu đồ.'),
          errorCode,
          showingPrevious: this.state.chart.state !== null,
        },
      })
    }
  }

  async loadChartBatch(maxPages = CHART_BATCH_MAX_PAGES): Promise<void> {
    const nodeId = this.state.nodeId
    if (!nodeId || this.state.history.timeMode !== 'known') return
    if (!this.state.chart.nextCursor || !this.state.chart.viewToken) return

    this.chartCtrl?.abort()
    const controller = new AbortController()
    this.chartCtrl = controller
    const expectedGen = ++this.chartGen

    this.emit({
      chart: {
        ...this.state.chart,
        loadingMore: true,
        loadingBatch: true,
        error: '',
        errorCode: null,
        showingPrevious: false,
      },
    })

    const limit = Math.max(1, Math.floor(maxPages))
    try {
      for (let pageIdx = 0; pageIdx < limit; pageIdx += 1) {
        const currentChart = this.state.chart
        if (!currentChart.nextCursor || !currentChart.viewToken) break
        const params = new URLSearchParams({
          page_size: String(currentChart.pageSize),
          cursor: currentChart.nextCursor,
          view_token: currentChart.viewToken,
        })
        const page = await getJson<MeasurementChartResponse>(
          `/api/v1/nodes/${encodeURIComponent(nodeId)}/measurements/chart?${params}`,
          controller.signal,
        )
        if (controller.signal.aborted || expectedGen !== this.chartGen) return
        const mergedPoints = [...this.state.chart.points, ...page.points]
        const hasMore = page.next_cursor !== null && pageIdx + 1 < limit
        this.emit({
          chart: {
            ...this.state.chart,
            loading: false,
            loadingMore: hasMore,
            loadingBatch: hasMore,
            error: '',
            errorCode: null,
            state: page.state,
            points: mergedPoints,
            nextCursor: page.next_cursor,
            viewToken: page.view_token,
            showingPrevious: false,
          },
        })
        if (page.next_cursor === null) break
      }
      if (!controller.signal.aborted && expectedGen === this.chartGen) {
        this.emit({
          chart: {
            ...this.state.chart,
            loadingMore: false,
            loadingBatch: false,
          },
        })
      }
    } catch (error) {
      if (error instanceof StaleResponse || isAbortOrStale(error)) return
      if (expectedGen !== this.chartGen) return
      const errorCode = error instanceof ApiError ? error.code : null
      this.emit({
        chart: {
          ...this.state.chart,
          loading: false,
          loadingMore: false,
          loadingBatch: false,
          error: describeApiError(error, 'Không tải tiếp được dữ liệu biểu đồ.'),
          errorCode,
          showingPrevious: this.state.chart.state !== null,
        },
      })
    }
  }

  cancelChartBatch() {
    if (!this.state.chart.loadingBatch && !this.state.chart.loadingMore) return
    this.abortChart()
    this.emit({
      chart: {
        ...this.state.chart,
        loadingMore: false,
        loadingBatch: false,
      },
    })
  }

  dispose() {
    this.currentGen += 1
    this.historyGen += 1
    this.currentCtrl?.abort()
    this.historyCtrl?.abort()
    this.currentCtrl = null
    this.historyCtrl = null
    this.abortChart()
    this.listeners.clear()
  }
}
