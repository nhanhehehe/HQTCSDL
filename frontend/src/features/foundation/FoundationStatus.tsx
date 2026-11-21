import { useEffect, useState } from 'react'
import { getJson } from '../../api/client'

type State = 'loading' | 'alive' | 'error'
type MonitoringHealth =
  | 'disabled'
  | 'starting'
  | 'running'
  | 'lock_unavailable'
  | 'schema_missing'
  | 'dead'

const MONITORING_LABELS: Record<MonitoringHealth, string> = {
  disabled: 'Tắt (disabled)',
  starting: 'Đang khởi động (starting)',
  running: 'Đang chạy (running)',
  lock_unavailable: 'Chờ/Mất applock (lock_unavailable)',
  schema_missing: 'Thiếu schema (schema_missing)',
  dead: 'Đã dừng/Lỗi (dead)',
}

export function FoundationStatus({ compact = false }: { compact?: boolean }) {
  const [state, setState] = useState<State>('loading')
  const [monitoring, setMonitoring] = useState<MonitoringHealth | null>(null)

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 5000)
    getJson<{ status: string; monitoring?: MonitoringHealth }>('/health/live', controller.signal)
      .then(data => {
        if (active) {
          setState(data.status === 'alive' ? 'alive' : 'error')
          setMonitoring(data.monitoring ?? null)
        }
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

  const monText = monitoring ? MONITORING_LABELS[monitoring] ?? monitoring : null

  if (compact) {
    return (
      <div className="stack-sm" aria-live="polite">
        <div className="health-inline">
          <span className={`health-dot health-dot-${state}`} aria-hidden="true" />
          <span>{text[state]}</span>
        </div>
        <small title="/health/live chỉ kiểm tra tiến trình API và trạng thái worker nội bộ; đọc dữ liệu SQL Server được phản ánh trực tiếp trên các trang nghiệp vụ.">
          {monText ? `Worker G4: ${monText}` : 'Chỉ kiểm tra tiến trình API (/health/live).'}
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
      <small>
        {monText
          ? `Trạng thái worker giám sát G4 (/health/live): ${monText}`
          : 'Chỉ kiểm tra tiến trình API. SQL Server và cảnh báo cần được kiểm tra riêng.'}
      </small>
    </section>
  )
}
