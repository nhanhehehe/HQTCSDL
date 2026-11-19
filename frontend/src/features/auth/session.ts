import { ApiError, invalidateRequests, onUnauthorized, requestJson } from '../../api/client'

export type Session = { admin: { id: string; username: string }; expires_at: string; csrf_token: string }
type State = {
  initialized: boolean
  session: Session | null
  busy: boolean
  message: string
  uncertainLogout: boolean
  verified: boolean
}

// The provider and mutation guards share this in-memory session lifecycle.
// Node tests exercise its asynchronous transitions without a second auth provider.
export class AuthSession {
  private state: State = {
    initialized: false,
    session: null,
    busy: false,
    message: '',
    uncertainLogout: false,
    verified: false,
  }
  private listeners = new Set<() => void>()
  private revision = 0
  private operation = 0
  private operating = false
  private timer: ReturnType<typeof setTimeout> | undefined
  private unsubscribe: (() => void) | undefined

  getSnapshot = () => this.state
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  private update(patch: Partial<State>) {
    this.state = { ...this.state, ...patch }
    for (const listener of this.listeners) listener()
  }

  private clear(message = '') {
    this.revision += 1
    invalidateRequests()
    clearTimeout(this.timer)
    this.update({ session: null, verified: false, message })
  }

  private accept(value: Session) {
    const remaining = Date.parse(value.expires_at) - Date.now()
    if (!Number.isFinite(remaining) || remaining <= 0) {
      this.clear('Phiên đã hết hạn. Vui lòng đăng nhập lại.')
      return
    }
    const previous = this.state.session
    if (previous && (previous.csrf_token !== value.csrf_token || previous.admin.id !== value.admin.id)) {
      this.clear()
    }
    clearTimeout(this.timer)
    this.update({ initialized: true, session: value, verified: true, message: '' })
    this.timer = setTimeout(() => this.clear('Phiên đã hết hạn. Vui lòng đăng nhập lại.'), remaining)
  }

  canWrite = () => this.state.verified && !this.state.busy && !this.state.uncertainLogout
    && this.state.session !== null && Date.parse(this.state.session.expires_at) > Date.now()

  start() {
    this.unsubscribe = onUnauthorized(() => this.clear('Phiên không hợp lệ. Vui lòng đăng nhập lại.'))
    void this.verify()
  }

  stop() {
    this.unsubscribe?.()
    this.unsubscribe = undefined
    this.operation += 1
    this.operating = false
    this.clear()
    this.update({ initialized: false, busy: false })
  }

  private begin() {
    if (this.operating) return null
    this.operating = true
    const active = ++this.operation
    this.update({ busy: true, verified: false })
    return active
  }

  private finish(active: number) {
    if (active === this.operation) {
      this.operating = false
      this.update({ initialized: true, busy: false })
    }
  }

  verify = async () => {
    if (this.state.uncertainLogout) return
    const active = this.begin()
    if (active === null) return
    // Revalidation preserves the form and its original rowversion. Only expiry,
    // 401, logout or a different session invalidate private requests and state.
    const expected = this.revision
    try {
      const value = await requestJson<Session>('/api/v1/auth/session')
      if (expected === this.revision) this.accept(value)
    } catch (error) {
      if (expected !== this.revision) return
      if (error instanceof ApiError && error.status === 401) {
        this.clear('Phiên không hợp lệ. Vui lòng đăng nhập lại.')
      } else {
        this.update({ message: 'Chưa xác minh được phiên. Thao tác lưu đang khóa; hãy kiểm tra phiên lại.' })
      }
    } finally {
      this.finish(active)
    }
  }

  login = async (username: string, password: string) => {
    if (this.state.uncertainLogout) return
    const active = this.begin()
    if (active === null) return
    this.clear()
    const expected = this.revision
    try {
      const value = await requestJson<Session>('/api/v1/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-HQ-Client': 'web' },
        body: JSON.stringify({ username, password }),
      })
      if (expected === this.revision) this.accept(value)
    } catch (error) {
      if (expected === this.revision) this.update({ message: error instanceof ApiError ? error.message
        : 'Chưa xác nhận được đăng nhập. Dùng Kiểm tra phiên trước khi gửi lại.' })
    } finally {
      this.finish(active)
    }
  }

  logout = async () => {
    const active = this.begin()
    if (active === null) return
    let csrf = this.state.session?.csrf_token
    this.clear()
    this.update({ uncertainLogout: true })
    try {
      // An explicit retry may obtain CSRF without restoring private UI.
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
      // A retry's session lookup can legitimately return 401 and clear again.
      // A confirmed logout still completes unless this operation was disposed.
      if (active === this.operation) {
        this.update({ uncertainLogout: false, message: 'Đã đăng xuất.' })
      }
    } catch {
      if (active === this.operation) this.update({ message: 'Chưa xác nhận được đăng xuất hoàn tất. Vui lòng thử đăng xuất lại.' })
    } finally {
      this.finish(active)
    }
  }
}
