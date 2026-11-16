import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError, invalidateRequests, onUnauthorized, requestJson } from '../../api/client'

type Session = { admin: { id: string; username: string }; expires_at: string; csrf_token: string }
type Auth = {
  session: Session | null; busy: boolean; message: string; uncertainLogout: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>; verify: () => Promise<void>
}
const Context = createContext<Auth | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [uncertainLogout, setUncertainLogout] = useState(false)
  const current = useRef<Session | null>(null)
  const operating = useRef(false)
  const logoutUnknown = useRef(false)
  const revision = useRef(0)
  const operation = useRef(0)

  const clear = useCallback((text = '') => {
    revision.current += 1
    invalidateRequests()
    current.current = null
    setSession(null)
    setMessage(text)
  }, [])

  const accept = useCallback((value: Session) => {
    if (!Number.isFinite(Date.parse(value.expires_at)) || Date.parse(value.expires_at) <= Date.now()) {
      clear('Phiên đã hết hạn. Vui lòng đăng nhập lại.')
      return
    }
    current.current = value
    setSession(value)
    setMessage('')
  }, [clear])

  const verify = useCallback(async () => {
    if (operating.current || logoutUnknown.current) return
    operating.current = true
    const active = ++operation.current
    setBusy(true)
    clear()
    const expected = revision.current
    try {
      const value = await requestJson<Session>('/api/v1/auth/session')
      if (expected === revision.current) accept(value)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return
      if (expected === revision.current) setMessage('Chưa xác minh được phiên. Kiểm tra kết nối rồi thử lại.')
    } finally {
      if (active === operation.current) {
        operating.current = false
        setBusy(false)
      }
    }
  }, [accept, clear])

  useEffect(() => {
    const unsubscribe = onUnauthorized(() => clear('Phiên không hợp lệ. Vui lòng đăng nhập lại.'))
    void verify()
    const resume = () => {
      if (document.visibilityState === 'visible') void verify()
    }
    window.addEventListener('focus', resume)
    document.addEventListener('visibilitychange', resume)
    return () => {
      unsubscribe()
      window.removeEventListener('focus', resume)
      document.removeEventListener('visibilitychange', resume)
      clear()
      operation.current += 1
      // StrictMode mounts again; allow its next verification to replace the cancelled request.
      operating.current = false
    }
  }, [clear, verify])

  useEffect(() => {
    if (!session) return
    const timer = window.setTimeout(() => clear('Phiên đã hết hạn. Vui lòng đăng nhập lại.'),
      Math.max(0, Date.parse(session.expires_at) - Date.now()))
    return () => window.clearTimeout(timer)
  }, [session, clear])

  async function login(username: string, password: string) {
    if (operating.current || logoutUnknown.current) return
    operating.current = true
    const active = ++operation.current
    setBusy(true)
    clear()
    const expected = revision.current
    try {
      const value = await requestJson<Session>('/api/v1/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-HQ-Client': 'web' },
        body: JSON.stringify({ username, password }),
      })
      if (expected === revision.current) accept(value)
    } catch (error) {
      if (expected === revision.current) setMessage(error instanceof ApiError ? error.message
        : 'Chưa xác nhận được đăng nhập. Dùng Kiểm tra phiên trước khi gửi lại.')
    } finally {
      if (active === operation.current) {
        operating.current = false
        setBusy(false)
      }
    }
  }

  async function logout() {
    if (operating.current) return
    operating.current = true
    const active = ++operation.current
    setBusy(true)
    let csrf = current.current?.csrf_token
    clear()
    const expected = revision.current
    logoutUnknown.current = true
    setUncertainLogout(true)
    try {
      // An explicit retry can read CSRF without restoring any private UI.
      if (!csrf) {
        try {
          csrf = (await requestJson<Session>('/api/v1/auth/session')).csrf_token
        } catch (error) {
          if (!(error instanceof ApiError && error.status === 401)) throw error
        }
      }
      await requestJson<void>('/api/v1/auth/logout', {
        method: 'POST', headers: csrf ? { 'X-CSRF-Token': csrf } : {},
      })
      logoutUnknown.current = false
      setUncertainLogout(false)
      setMessage('Đã đăng xuất.')
    } catch {
      if (expected <= revision.current) setMessage('Chưa xác nhận được đăng xuất hoàn tất. Vui lòng thử đăng xuất lại.')
    } finally {
      if (active === operation.current) {
        operating.current = false
        setBusy(false)
      }
    }
  }

  return <Context.Provider value={{ session, busy, message, uncertainLogout, login, logout, verify }}>
    {children}
  </Context.Provider>
}

export function useAuth() {
  const value = useContext(Context)
  if (!value) throw new Error('AuthProvider is required')
  return value
}
