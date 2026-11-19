import { useEffect, useState } from 'react'
import { getJson } from '../../api/client'

type State = 'loading' | 'alive' | 'error'

export function FoundationStatus({ compact = false }: { compact?: boolean }) {
  const [state, setState] = useState<State>('loading')

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 5000)
    getJson<{ status: string }>('/health/live', controller.signal)
      .then(data => {
        if (active) setState(data.status === 'alive' ? 'alive' : 'error')
      })
      .catch(() => {
        if (active) setState('error')
      })
      .finally(() => clearTimeout(timer))
    return () => {
      active = false
      controller.abort()
      clearTimeout(timer)
    }
  }, [])

  const text = {
    loading: 'Đang kết nối Backend…',
    alive: 'Backend đang phản hồi',
    error: 'Chưa kết nối được Backend',
  }

  if (compact) {
    return (
      <div className="stack-sm" aria-live="polite">
        <div className="health-inline">
          <span className={`health-dot health-dot-${state}`} aria-hidden="true" />
          <span>{text[state]}</span>
        </div>
        <small title="/health/live chỉ kiểm tra tiến trình API. SQL Server và cảnh báo cần được kiểm tra riêng.">
          Chỉ kiểm tra tiến trình API (/health/live).
        </small>
      </div>
    )
  }

  return (
    <section className="card" aria-live="polite">
      <h2>Kết nối ứng dụng</h2>
      <div className="health-inline">
        <span className={`health-dot health-dot-${state}`} aria-hidden="true" />
        <p style={{ margin: 0 }}>{text[state]}</p>
      </div>
      <small>Chỉ kiểm tra tiến trình API. SQL Server và cảnh báo cần được kiểm tra riêng.</small>
    </section>
  )
}
