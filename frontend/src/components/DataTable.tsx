import type { ReactNode } from 'react'
import { EmptyState } from './EmptyState'

export type Column<T> = {
  key: string
  header: string
  render: (item: T) => ReactNode
  className?: string
}

export function DataTable<T>({
  columns,
  items,
  getKey,
  selectedKey,
  loading = false,
  loaded = true,
  emptyTitle = 'Chưa có dữ liệu',
  emptyDescription,
  caption,
}: {
  columns: ReadonlyArray<Column<T>>
  items: ReadonlyArray<T>
  getKey: (item: T) => string
  selectedKey?: string | null
  loading?: boolean
  loaded?: boolean
  emptyTitle?: string
  emptyDescription?: string
  caption?: string
}) {
  if (!loaded && loading) {
    return <p className="muted" role="status">Đang tải dữ liệu…</p>
  }

  if (loaded && items.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        {caption && <caption className="muted">{caption}</caption>}
        <thead>
          <tr>
            {columns.map(col => (
              <th key={col.key} scope="col" className={col.className}>
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map(item => {
            const key = getKey(item)
            return (
              <tr key={key} className={selectedKey === key ? 'selected' : undefined}>
                {columns.map(col => (
                  <td key={col.key} className={col.className}>
                    {col.render(item)}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
