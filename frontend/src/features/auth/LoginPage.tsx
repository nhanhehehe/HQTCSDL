import { useState } from 'react'
import type { FormEvent } from 'react'
import { StateBanner } from '../../components/StateBanner'
import { FoundationStatus } from '../foundation/FoundationStatus'
import { useAuth } from './AuthProvider'

export function LoginPage() {
  const auth = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    const entered = password
    setPassword('')
    void auth.login(username, entered)
  }

  return (
    <div className="login-shell">
      <div className="login-container">
        <div>
          <p className="eyebrow">HQTCSDL · GIÁM SÁT NÔNG TRẠI</p>
          <h1>Hệ thống giám sát IoT</h1>
        </div>

        <section className="card stack" aria-busy={auth.busy}>
          <h2>{auth.uncertainLogout ? 'Xác nhận đăng xuất' : 'Đăng nhập quản trị'}</h2>

          {auth.message && <StateBanner tone="warn" message={auth.message} />}

          {auth.uncertainLogout ? (
            <div className="stack-sm">
              <p>
                Phiên làm việc trước đó chưa xác nhận đã đăng xuất hoàn tất với Backend. Vui lòng thử
                đăng xuất lại để dọn phiên an toàn.
              </p>
              <button disabled={auth.busy} onClick={() => void auth.logout()}>
                Thử đăng xuất lại
              </button>
            </div>
          ) : (
            <>
              <form className="stack" onSubmit={submit}>
                <label>
                  Tên đăng nhập
                  <input
                    required
                    maxLength={128}
                    autoComplete="username"
                    value={username}
                    disabled={auth.busy}
                    onChange={event => setUsername(event.target.value)}
                  />
                </label>
                <label>
                  Mật khẩu
                  <input
                    required
                    maxLength={1024}
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    disabled={auth.busy}
                    onChange={event => setPassword(event.target.value)}
                  />
                </label>
                <button disabled={auth.busy} type="submit">
                  Đăng nhập
                </button>
              </form>
              <button
                type="button"
                className="btn-secondary"
                disabled={auth.busy}
                onClick={() => void auth.verify()}
              >
                Kiểm tra phiên hiện có
              </button>
            </>
          )}

          {auth.busy && (
            <p className="muted" role="status">
              Đang xác minh…
            </p>
          )}
        </section>

        <div className="card">
          <FoundationStatus compact />
        </div>
      </div>
    </div>
  )
}
