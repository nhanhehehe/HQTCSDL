import { useId, useRef } from 'react'
import type { ReactNode } from 'react'
import { useDialogFocus } from './focusUtils'

type ModalProps = {
  open: boolean
  title: string
  onClose: () => void
  busy?: boolean
  disableBackdropClose?: boolean
  hideCloseButton?: boolean
  children: ReactNode
  footer?: ReactNode
}

export function Modal({
  open,
  title,
  onClose,
  busy = false,
  disableBackdropClose = false,
  hideCloseButton = false,
  children,
  footer,
}: ModalProps) {
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement>(null)

  useDialogFocus(open, panelRef, onClose, busy)

  if (!open) return null

  return (
    <div
      className="dialog-backdrop dialog-backdrop-center"
      onMouseDown={event => {
        if (event.target === event.currentTarget && !busy && !disableBackdropClose) {
          onClose()
        }
      }}
    >
      <div
        ref={panelRef}
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="dialog-header">
          <h2 id={titleId}>{title}</h2>
          {!hideCloseButton && (
            <button
              type="button"
              className="btn-ghost btn-sm"
              aria-label="Đóng hộp thoại"
              disabled={busy}
              onClick={onClose}
            >
              Đóng
            </button>
          )}
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-footer">{footer}</div>}
      </div>
    </div>
  )
}
