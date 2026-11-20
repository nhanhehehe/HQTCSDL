import { requestJson } from '../../api/client'
import type { FarmView, ZoneView } from './AreasPanel'

export async function fetchAllActivePages<T>(
  basePath: string,
  signal?: AbortSignal,
): Promise<T[]> {
  const all: T[] = []
  let nextCursor: string | null = null
  do {
    const params = new URLSearchParams({ status: 'ACTIVE', page_size: '10' })
    if (nextCursor) params.set('cursor', nextCursor)
    const page = await requestJson<{ items: T[]; next_cursor: string | null }>(
      `${basePath}?${params}`,
      { signal },
    )
    if (signal?.aborted) break
    all.push(...page.items)
    nextCursor = page.next_cursor
  } while (nextCursor !== null && !signal?.aborted)
  return all
}

export class FarmZoneLoader {
  private zoneGeneration = 0
  private zoneController: AbortController | null = null

  async loadActiveFarms(signal?: AbortSignal): Promise<FarmView[]> {
    return fetchAllActivePages<FarmView>('/api/v1/farms', signal)
  }

  selectFarm(
    nextFarmId: string,
    callbacks: {
      onReset: (farmId: string) => void
      onLoaded: (zones: ZoneView[]) => void
      onError: () => void
    },
  ): Promise<void> {
    this.zoneGeneration += 1
    this.zoneController?.abort()
    this.zoneController = null
    callbacks.onReset(nextFarmId)
    if (!nextFarmId) return Promise.resolve()

    const activeGen = this.zoneGeneration
    const controller = new AbortController()
    this.zoneController = controller

    return fetchAllActivePages<ZoneView>(`/api/v1/farms/${nextFarmId}/zones`, controller.signal)
      .then(allZones => {
        if (controller.signal.aborted || activeGen !== this.zoneGeneration) return
        callbacks.onLoaded(allZones)
      })
      .catch(() => {
        if (controller.signal.aborted || activeGen !== this.zoneGeneration) return
        callbacks.onError()
      })
  }

  dispose() {
    this.zoneGeneration += 1
    this.zoneController?.abort()
    this.zoneController = null
  }
}
