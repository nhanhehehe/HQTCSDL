import { ApiError, StaleResponse, requestJson } from '../api/client'
import type { FarmPage, FarmView } from '../features/areas/types'

export type OperationalFarmState = {
  farms: FarmView[]
  selectedFarmId: string
  contextGeneration: number
  loading: boolean
  error: string
}

export class OperationalFarmStore {
  private state: OperationalFarmState = {
    farms: [],
    selectedFarmId: '',
    contextGeneration: 0,
    loading: false,
    error: '',
  }

  private listeners = new Set<() => void>()
  private loadGeneration = 0
  private controller: AbortController | null = null

  getSnapshot = () => this.state

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private update(patch: Partial<OperationalFarmState>) {
    this.state = { ...this.state, ...patch }
    for (const listener of this.listeners) listener()
  }

  getSelectedFarm(): FarmView | null {
    if (!this.state.selectedFarmId) return null
    return this.state.farms.find(f => f.id === this.state.selectedFarmId) ?? null
  }

  async loadFarms(): Promise<void> {
    this.controller?.abort()
    const controller = new AbortController()
    this.controller = controller
    const activeGen = ++this.loadGeneration
    this.update({ loading: true, error: '' })

    try {
      const all: FarmView[] = []
      let nextCursor: string | null = null
      do {
        const params = new URLSearchParams({ status: 'ALL', page_size: '50' })
        if (nextCursor) params.set('cursor', nextCursor)
        const page = await requestJson<FarmPage>(`/api/v1/farms?${params}`, {
          signal: controller.signal,
        })
        if (controller.signal.aborted || activeGen !== this.loadGeneration) return
        all.push(...page.items)
        nextCursor = page.next_cursor
      } while (nextCursor !== null && !controller.signal.aborted)

      if (controller.signal.aborted || activeGen !== this.loadGeneration) return

      const currentSelected = this.state.selectedFarmId
      const stillExists = currentSelected !== '' && all.some(f => f.id === currentSelected)
      const nextSelectedId = stillExists ? currentSelected : ''
      const bumped = currentSelected !== '' && !stillExists

      this.update({
        farms: all,
        selectedFarmId: nextSelectedId,
        contextGeneration: bumped
          ? this.state.contextGeneration + 1
          : this.state.contextGeneration,
        loading: false,
        error: '',
      })
    } catch (error) {
      if (controller.signal.aborted || activeGen !== this.loadGeneration) return
      if (error instanceof StaleResponse) return
      this.update({
        loading: false,
        error: error instanceof ApiError ? error.message : 'Không tải được danh sách Farm từ Backend.',
      })
    }
  }

  selectFarm = (nextFarmId: string) => {
    if (nextFarmId !== '' && !this.state.farms.some(f => f.id === nextFarmId)) {
      return
    }
    if (nextFarmId === this.state.selectedFarmId) return
    this.update({
      selectedFarmId: nextFarmId,
      contextGeneration: this.state.contextGeneration + 1,
    })
  }

  dispose() {
    this.loadGeneration += 1
    this.controller?.abort()
    this.controller = null
    this.listeners.clear()
  }
}
