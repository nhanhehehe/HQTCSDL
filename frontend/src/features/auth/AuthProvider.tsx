import { createContext, useContext, useEffect, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import { AuthSession } from './session'

type Auth = ReturnType<AuthSession['getSnapshot']> & {
  login: AuthSession['login']; logout: AuthSession['logout']; verify: AuthSession['verify']
  canWrite: AuthSession['canWrite']
}
const Context = createContext<Auth | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth] = useState(() => new AuthSession())
  const state = useSyncExternalStore(auth.subscribe, auth.getSnapshot)

  useEffect(() => {
    auth.start()
    const resume = () => {
      if (document.visibilityState === 'visible') void auth.verify()
    }
    window.addEventListener('focus', resume)
    document.addEventListener('visibilitychange', resume)
    return () => {
      window.removeEventListener('focus', resume)
      document.removeEventListener('visibilitychange', resume)
      auth.stop()
    }
  }, [auth])

  return <Context.Provider value={{ ...state, login: auth.login, logout: auth.logout,
    verify: auth.verify, canWrite: auth.canWrite }}>{children}</Context.Provider>
}

export function useAuth() {
  const value = useContext(Context)
  if (!value) throw new Error('AuthProvider is required')
  return value
}
