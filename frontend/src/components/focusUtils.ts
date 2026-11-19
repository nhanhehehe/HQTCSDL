import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

const dialogStack: HTMLElement[] = []

export function getFocusableElements(container: HTMLElement): HTMLElement[] {
  const candidates = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
  return candidates.filter(el => {
    if (el.hasAttribute('disabled') || el.getAttribute('aria-hidden') === 'true') return false
    return el.getClientRects().length > 0
  })
}

export function useDialogFocus(
  open: boolean,
  containerRef: RefObject<HTMLElement | null>,
  onRequestClose: () => void,
  busy = false,
) {
  const onCloseRef = useRef(onRequestClose)
  const busyRef = useRef(busy)
  const previousFocusRef = useRef<HTMLElement | null>(null)

  onCloseRef.current = onRequestClose
  busyRef.current = busy

  useEffect(() => {
    if (!open) return
    const container = containerRef.current
    if (!container) return

    previousFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialogStack.push(container)

    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const focusTimer = requestAnimationFrame(() => {
      if (!container.isConnected) return
      const focusable = getFocusableElements(container)
      const target = focusable[0] ?? container
      target.focus()
    })

    function handleKeyDown(event: KeyboardEvent) {
      if (!container) return
      const top = dialogStack[dialogStack.length - 1]
      if (top !== container) return

      if (event.key === 'Escape') {
        event.preventDefault()
        if (!busyRef.current) {
          onCloseRef.current()
        }
        return
      }

      if (event.key === 'Tab') {
        const focusable = getFocusableElements(container)
        if (focusable.length === 0) {
          event.preventDefault()
          container.focus()
          return
        }
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        const active = document.activeElement
        const inside = active instanceof Node && container.contains(active)

        if (event.shiftKey) {
          if (!inside || active === first || active === container) {
            event.preventDefault()
            last.focus()
          }
        } else if (!inside || active === last) {
          event.preventDefault()
          first.focus()
        }
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      cancelAnimationFrame(focusTimer)
      document.removeEventListener('keydown', handleKeyDown)
      const idx = dialogStack.lastIndexOf(container)
      if (idx !== -1) dialogStack.splice(idx, 1)
      if (dialogStack.length === 0) {
        document.body.style.overflow = prevOverflow
      }
      const prev = previousFocusRef.current
      previousFocusRef.current = null
      if (prev && prev.isConnected) {
        prev.focus()
      }
    }
  }, [open, containerRef])
}
