export type AreaStatus = 'ACTIVE' | 'ARCHIVED' | 'ALL'

export type FarmView = {
  id: string
  code: string
  name: string
  description: string | null
  archived_at: string | null
  row_version: string
}

export type ZoneView = FarmView & { farm_id: string }

export type FarmPage = { items: FarmView[]; next_cursor: string | null }
export type ZonePage = { items: ZoneView[]; next_cursor: string | null }
