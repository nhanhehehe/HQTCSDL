export type StageTag = 'G1' | 'G3' | 'G4' | 'G3/G4'

export type NavGroupId = 'operations' | 'admin'

export type NavItem = {
  path: string
  label: string
  group: NavGroupId
  stage: StageTag
  operational: boolean
}

export const DEFAULT_AUTHENTICATED_ROUTE = '/dashboard'

export const NAV_GROUPS: ReadonlyArray<{ id: NavGroupId; title: string; items: ReadonlyArray<NavItem> }> = [
  {
    id: 'operations',
    title: 'Vận hành',
    items: [
      { path: '/dashboard', label: 'Tổng quan', group: 'operations', stage: 'G3/G4', operational: true },
      { path: '/measurements', label: 'Dữ liệu đo', group: 'operations', stage: 'G3', operational: true },
      { path: '/alerts', label: 'Cảnh báo', group: 'operations', stage: 'G4', operational: true },
      { path: '/reports', label: 'Báo cáo', group: 'operations', stage: 'G3', operational: true },
    ],
  },
  {
    id: 'admin',
    title: 'Cấu hình & Quản trị',
    items: [
      { path: '/monitoring', label: 'Giám sát & Ngưỡng', group: 'admin', stage: 'G4', operational: true },
      { path: '/areas', label: 'Farm & Zone', group: 'admin', stage: 'G1', operational: false },
      { path: '/devices', label: 'Thiết bị & Khóa', group: 'admin', stage: 'G1', operational: false },
    ],
  },
]

function normalizePath(pathname: string): string {
  if (!pathname || pathname === '/') return '/'
  return pathname.endsWith('/') ? pathname.slice(0, -1) : pathname
}

export function isOperationalRoute(pathname: string): boolean {
  const path = normalizePath(pathname)
  if (path === '/alerts' || path.startsWith('/alerts/')) return true
  return (
    path === '/dashboard' ||
    path === '/measurements' ||
    path === '/reports' ||
    path === '/monitoring'
  )
}

export function getRouteTitle(pathname: string): string {
  const path = normalizePath(pathname)
  if (path === '/login') return 'Đăng nhập quản trị'
  if (path.startsWith('/alerts/')) return 'Chi tiết cảnh báo & Audit'
  for (const group of NAV_GROUPS) {
    const match = group.items.find(item => item.path === path)
    if (match) return match.label
  }
  return 'Hệ thống giám sát nông trại'
}

export type AuthViewState =
  | 'initializing'
  | 'uncertain_logout'
  | 'authenticated'
  | 'unauthenticated'

export function resolveAuthView(state: {
  initialized: boolean
  session: unknown | null
  uncertainLogout: boolean
}): AuthViewState {
  if (state.uncertainLogout) return 'uncertain_logout'
  if (!state.initialized) return 'initializing'
  if (state.session !== null) return 'authenticated'
  return 'unauthenticated'
}
