import type { ReactNode } from 'react'

export type BannerTone = 'info' | 'ok' | 'warn' | 'err'

export function StateBanner({
  tone = 'info',
  message,
  action,
}: {
  tone?: BannerTone
  message: ReactNode
  action?: ReactNode
}) {
  return (
    <div className={`banner banner-${tone}`} role={tone === 'err' ? 'alert' : 'status'}>
      <div>{message}</div>
      {action && <div className="row">{action}</div>}
    </div>
  )
}
