import { ApiError, StaleResponse, requestCsvBlob, requestJson } from '../../api/client'
import type { CsvDownloadResult } from '../../api/client'
import { viDatetimeInputToIso } from '../measurements/format'
import { describeApiError, isAbortOrStale } from '../measurements/scopeLoader'
import type { ReportView } from './types'

export type ReportDraft = {
  nodeId: string
  fromInput: string
  toInput: string
}

export type ReportsStoreState = {
  draft: ReportDraft
  result: ReportView | null
  resultMatchesDraft: boolean
  creating: boolean
  exporting: boolean
  error: string
  exportError: string
  exportSuccess: string
  contextGeneration: number
}

function sameDraft(a: ReportDraft, b: ReportDraft): boolean {
  return a.nodeId === b.nodeId && a.fromInput === b.fromInput && a.toInput === b.toInput
}

export class ReportsStore {
  private state: ReportsStoreState
  private listeners = new Set<() => void>()
  private requestSeq = 0
  private createCtrl: AbortController | null = null
  private exportCtrl: AbortController | null = null

  constructor() {
    this.state = {
      draft: {
        nodeId: '',
        fromInput: '',
        toInput: '',
      },
      result: null,
      resultMatchesDraft: false,
      creating: false,
      exporting: false,
      error: '',
      exportError: '',
      exportSuccess: '',
      contextGeneration: 0,
    }
  }

  getSnapshot = () => this.state

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private emit(patch: Partial<ReportsStoreState>) {
    this.state = { ...this.state, ...patch }
    for (const listener of this.listeners) listener()
  }

  resetForFarmChange(contextGeneration: number) {
    this.requestSeq += 1
    this.createCtrl?.abort()
    this.exportCtrl?.abort()
    this.createCtrl = null
    this.exportCtrl = null
    this.emit({
      draft: { ...this.state.draft, nodeId: '' },
      result: null,
      resultMatchesDraft: false,
      creating: false,
      exporting: false,
      error: '',
      exportError: '',
      exportSuccess: '',
      contextGeneration,
    })
  }

  setNodeId(nodeId: string) {
    if (nodeId === this.state.draft.nodeId) return
    this.emit({
      draft: { ...this.state.draft, nodeId },
      resultMatchesDraft: false,
      error: '',
      exportError: '',
      exportSuccess: '',
    })
  }

  setFromInput(fromInput: string) {
    if (fromInput === this.state.draft.fromInput) return
    this.emit({
      draft: { ...this.state.draft, fromInput },
      resultMatchesDraft: false,
      error: '',
      exportError: '',
      exportSuccess: '',
    })
  }

  setToInput(toInput: string) {
    if (toInput === this.state.draft.toInput) return
    this.emit({
      draft: { ...this.state.draft, toInput },
      resultMatchesDraft: false,
      error: '',
      exportError: '',
      exportSuccess: '',
    })
  }

  applyDefault24h() {
    if (this.state.draft.fromInput === '' && this.state.draft.toInput === '') return
    this.emit({
      draft: {
        ...this.state.draft,
        fromInput: '',
        toInput: '',
      },
      resultMatchesDraft: false,
      error: '',
      exportError: '',
      exportSuccess: '',
    })
  }

  canExportCsv(canWrite: boolean): boolean {
    return (
      canWrite &&
      !this.state.creating &&
      !this.state.exporting &&
      this.state.result !== null &&
      this.state.resultMatchesDraft
    )
  }

  async createReport(csrf: string, canWrite: boolean): Promise<boolean> {
    if (!canWrite) {
      this.emit({ error: 'Phiên chưa được xác minh hợp lệ để tạo báo cáo.' })
      return false
    }
    const submittedDraft = { ...this.state.draft }
    if (!submittedDraft.nodeId) {
      this.emit({ error: 'Vui lòng chọn Node trước khi tạo báo cáo.' })
      return false
    }

    let fromIso = ''
    let toIso = ''
    try {
      fromIso = viDatetimeInputToIso(submittedDraft.fromInput)
      toIso = viDatetimeInputToIso(submittedDraft.toInput)
    } catch (error) {
      this.emit({ error: describeApiError(error, 'Định dạng thời gian không hợp lệ.') })
      return false
    }

    this.createCtrl?.abort()
    const controller = new AbortController()
    this.createCtrl = controller
    const seq = ++this.requestSeq
    const expectedCtxGen = this.state.contextGeneration

    const body: Record<string, string> = { node_id: submittedDraft.nodeId }
    if (fromIso) body.from = fromIso
    if (toIso) body.to = toIso

    this.emit({
      creating: true,
      error: '',
      exportError: '',
      exportSuccess: '',
    })

    try {
      const report = await requestJson<ReportView>('/api/v1/reports/measurements', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-HQ-Client': 'web',
          'X-CSRF-Token': csrf,
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (
        controller.signal.aborted ||
        seq !== this.requestSeq ||
        expectedCtxGen !== this.state.contextGeneration
      ) {
        return false
      }
      const matchesCurrentDraft = sameDraft(this.state.draft, submittedDraft)
      this.emit({
        creating: false,
        result: report,
        resultMatchesDraft: matchesCurrentDraft,
        error: '',
      })
      return true
    } catch (error) {
      if (error instanceof StaleResponse) {
        this.emit({
          result: null,
          resultMatchesDraft: false,
          creating: false,
          error: '',
        })
        return false
      }
      if (
        isAbortOrStale(error) ||
        seq !== this.requestSeq ||
        expectedCtxGen !== this.state.contextGeneration
      ) {
        return false
      }
      this.emit({
        creating: false,
        error: describeApiError(error, 'Không tạo được báo cáo thống kê.'),
      })
      return false
    } finally {
      if (this.createCtrl === controller) {
        this.createCtrl = null
      }
    }
  }

  async exportCsv(csrf: string, canWrite: boolean): Promise<CsvDownloadResult | null> {
    if (!this.canExportCsv(canWrite) || !this.state.result) return null
    const pinnedToken = this.state.result.snapshot_token
    const expectedCtxGen = this.state.contextGeneration

    this.exportCtrl?.abort()
    const controller = new AbortController()
    this.exportCtrl = controller

    this.emit({
      exporting: true,
      exportError: '',
      exportSuccess: '',
    })

    try {
      const csv = await requestCsvBlob('/api/v1/reports/measurements/csv', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-HQ-Client': 'web',
          'X-CSRF-Token': csrf,
        },
        body: JSON.stringify({ snapshot_token: pinnedToken }),
        signal: controller.signal,
      })
      if (
        controller.signal.aborted ||
        expectedCtxGen !== this.state.contextGeneration ||
        this.state.result?.snapshot_token !== pinnedToken ||
        !this.state.resultMatchesDraft
      ) {
        return null
      }
      this.emit({
        exporting: false,
        exportError: '',
        exportSuccess: `Đã tải tệp ${csv.filename}.`,
      })
      return csv
    } catch (error) {
      if (error instanceof StaleResponse) {
        this.emit({
          result: null,
          resultMatchesDraft: false,
          exporting: false,
          exportError: '',
        })
        return null
      }
      if (isAbortOrStale(error) || expectedCtxGen !== this.state.contextGeneration) {
        return null
      }
      let message = describeApiError(error, 'Không xuất được tệp CSV.')
      if (error instanceof ApiError && error.code === 'SNAPSHOT_EXPIRED') {
        message =
          'Snapshot báo cáo đã hết hiệu lực (410 SNAPSHOT_EXPIRED). Vui lòng bấm Tạo báo cáo lại.'
      } else if (error instanceof ApiError && error.code === 'REPORT_CONTEXT_CHANGED') {
        message =
          'Ngữ cảnh dữ liệu đã thay đổi (409 REPORT_CONTEXT_CHANGED). Vui lòng bấm Tạo báo cáo lại.'
      }
      this.emit({
        exporting: false,
        exportError: message,
      })
      return null
    } finally {
      if (this.exportCtrl === controller) {
        this.exportCtrl = null
      }
    }
  }

  dispose() {
    this.requestSeq += 1
    this.createCtrl?.abort()
    this.exportCtrl?.abort()
    this.createCtrl = null
    this.exportCtrl = null
    this.listeners.clear()
  }
}
