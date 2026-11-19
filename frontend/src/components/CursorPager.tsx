export function CursorPager({
  count,
  nextCursor,
  busy = false,
  onLoadMore,
}: {
  count: number
  nextCursor: string | null
  busy?: boolean
  onLoadMore: () => void
}) {
  return (
    <div className="cursor-pager">
      <small className="tabular">Đang hiển thị {count} mục{nextCursor ? ' (còn dữ liệu trang sau)' : ''}</small>
      {nextCursor && (
        <button type="button" className="btn-secondary btn-sm" disabled={busy} onClick={onLoadMore}>
          {busy ? 'Đang tải…' : 'Tải thêm'}
        </button>
      )}
    </div>
  )
}
