import { useCallback, useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useDialogFocus } from './focusUtils'

type DrawerProps = {
  open: boolean
  title: string
  onClose: () => void
  busy?: boolean
  dirty?: boolean
  children: ReactNode
  footer?: ReactNode
}

export function Drawer({
  open,
  title,
  onClose,
  busy = false,
  dirty = false,
  children,
  footer,
}: DrawerProps) {
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement>(null)
  const [confirmDiscard, setConfirmDiscard] = useState(false)

  useEffect(() => {
    if (!open || !dirty) {
      setConfirmDiscard(false)
    }
  }, [open, dirty])

  const requestClose = useCallback(() => {
    if (busy) return
    if (dirty && !confirmDiscard) {
      setConfirmDiscard(true)
      return
    }
    setConfirmDiscard(false)
    onClose()
  }, [busy, dirty, confirmDiscard, onClose])

  useDialogFocus(open, panelRef, requestClose, busy)

  if (!open) return null

  return (
    <div
      className="dialog-backdrop dialog-backdrop-right"
      onMouseDown={event => {
        if (event.target === event.currentTarget) {
          requestClose()
        }
      }}
    >
      <div
        ref={panelRef}
        className="drawer-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="dialog-header">
          <h2 id={titleId}>{title}</h2>
          <button
            type="button"
            className="btn-ghost btn-sm"
            aria-label="Đóng khung nhập"
            disabled={busy}
            onClick={requestClose}
          >
            Đóng
          </button>
        </div>
        <div className="dialog-body">
          {confirmDiscard && (
            <div className="banner banner-warn" role="alert">
              <div className="stack-sm">
                <strong>Bạn có thay đổi chưa lưu.</strong>
                <span>Đóng khung nhập sẽ bỏ các dữ liệu đang nhập trong biểu mẫu này.</span>
                <div className="row">
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => setConfirmDiscard(false)}
                  >
                    Tiếp tục chỉnh sửa
                  </button>
                  <button
                    type="button"
                    className="btn-danger btn-sm"
                    onClick={() => {
                      setConfirmDiscard(false)
                      onClose()
                    }}
                  >
                    Đóng và bỏ thay đổi
                  </button>
                </div>
              </div>
            </div>
          )}
          {children}
        </div>
        {footer && <div className="dialog-footer">{footer}</div>}
      </div>
    </div>
  )
}
