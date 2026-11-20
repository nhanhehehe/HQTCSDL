import { ApiError, StaleResponse, requestJson } from '../../api/client'
import type { ZonePage, ZoneView } from '../areas/types'
import type { NodeChoice } from './types'

type NodePage = {
  items: Array<{
    id: string
    device_id: string
    name: string
    zone: { id: string; code: string; name: string }
    receiving_enabled: boolean
  }>
  next_cursor: string | null
}

export type FarmNodesLoadResult = {
  zones: ZoneView[]
  nodes: NodeChoice[]
}

export class FarmNodesLoader {
  private generation = 0
  private controller: AbortController | null = null

  async load(farmId: string): Promise<FarmNodesLoadResult | null> {
    this.generation += 1
    this.controller?.abort()
    if (!farmId) {
      this.controller = null
      return { zones: [], nodes: [] }
    }
    const activeGen = this.generation
    const controller = new AbortController()
    this.controller = controller

    const zones: ZoneView[] = []
    let zoneCursor: string | null = null
    do {
      const params = new URLSearchParams({ status: 'ALL', page_size: '100' })
      if (zoneCursor) params.set('cursor', zoneCursor)
      const page = await requestJson<ZonePage>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/zones?${params}`,
        { signal: controller.signal },
      )
      if (controller.signal.aborted || activeGen !== this.generation) return null
      zones.push(...page.items)
      zoneCursor = page.next_cursor
    } while (zoneCursor !== null)

    const zoneMap = new Map(zones.map(z => [z.id, z]))
    const nodes: NodeChoice[] = []
    let nodeCursor: string | null = null
    do {
      const params = new URLSearchParams({ page_size: '200' })
      if (nodeCursor) params.set('cursor', nodeCursor)
      const page = await requestJson<NodePage>(`/api/v1/nodes?${params}`, {
        signal: controller.signal,
      })
      if (controller.signal.aborted || activeGen !== this.generation) return null
      for (const item of page.items) {
        const zone = zoneMap.get(item.zone.id)
        if (zone) {
          nodes.push({
            id: item.id,
            device_id: item.device_id,
            name: item.name,
            zone: {
              id: zone.id,
              code: zone.code,
              name: zone.name,
              archived_at: zone.archived_at,
            },
            receiving_enabled: item.receiving_enabled,
          })
        }
      }
      nodeCursor = page.next_cursor
    } while (nodeCursor !== null)

    if (controller.signal.aborted || activeGen !== this.generation) return null
    return { zones, nodes }
  }

  dispose() {
    this.generation += 1
    this.controller?.abort()
    this.controller = null
  }
}

export function isAbortOrStale(error: unknown): boolean {
  return (
    error instanceof StaleResponse ||
    (error instanceof DOMException && error.name === 'AbortError') ||
    (error instanceof Error && error.name === 'AbortError')
  )
}

export function describeApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}
