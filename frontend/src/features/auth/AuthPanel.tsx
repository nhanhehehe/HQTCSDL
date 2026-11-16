import { useState } from 'react'
import type { FormEvent } from 'react'
import { useAuth } from './AuthProvider'

export function AuthPanel() {
  const auth = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    const entered = password
    setPassword('')
    void auth.login(username, entered)
  }

  return <section aria-busy={auth.busy}>
    <h2>{auth.session ? 'Phiên quản trị' : 'Đăng nhập quản trị'}</h2>
    {auth.message && <p role="status">{auth.message}</p>}
    {auth.session ? <>
      <p>Xin chào {auth.session.admin.username}.</p>
      <p>Phiên hết hạn lúc {new Date(auth.session.expires_at).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })} (UTC+07:00).</p>
      <p>Đã xác thực. Các chức năng quản trị sẽ được bổ sung theo phạm vi tiếp theo.</p>
      <button disabled={auth.busy} onClick={() => void auth.logout()}>Đăng xuất</button>
    </> : auth.uncertainLogout ?
      <button disabled={auth.busy} onClick={() => void auth.logout()}>Thử đăng xuất lại</button> : <>
        <form onSubmit={submit}>
          <label>Tên đăng nhập<input required maxLength={128} autoComplete="username" value={username}
            disabled={auth.busy} onChange={event => setUsername(event.target.value)} /></label>
          <label>Mật khẩu<input required maxLength={1024} type="password" autoComplete="current-password"
            value={password} disabled={auth.busy} onChange={event => setPassword(event.target.value)} /></label>
          <button disabled={auth.busy} type="submit">Đăng nhập</button>
        </form>
        <button className="secondary" disabled={auth.busy} onClick={() => void auth.verify()}>Kiểm tra phiên</button>
      </>}
    {auth.busy && <p role="status">Đang xác minh…</p>}
  </section>
}
