import { createContext, useContext, useEffect, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import type { FarmView } from '../features/areas/types'
import { isOperationalRoute } from './navigation'
import { OperationalFarmStore } from './operationalFarmStore'
import type { OperationalFarmState } from './operationalFarmStore'

type OperationalFarmContextValue = OperationalFarmState & {
  selectedFarm: FarmView | null
  selectFarm: (farmId: string) => void
  reloadFarms: () => Promise<void>
}

const Context = createContext<OperationalFarmContextValue | null>(null)

export function OperationalFarmProvider({ children }: { children: ReactNode }) {
  const [store] = useState(() => new OperationalFarmStore())
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot)

  useEffect(() => {
    void store.loadFarms()
    return () => store.dispose()
  }, [store])

  const selectedFarm = state.farms.find(f => f.id === state.selectedFarmId) ?? null

  return (
    <Context.Provider
      value={{
        ...state,
        selectedFarm,
        selectFarm: store.selectFarm,
        reloadFarms: () => store.loadFarms(),
      }}
    >
      {children}
    </Context.Provider>
  )
}

export function useOptionalOperationalFarm(): OperationalFarmContextValue | null {
  return useContext(Context)
}

export function OperationalFarmSelector() {
  const location = useLocation()
  const farmCtx = useOptionalOperationalFarm()

  if (!farmCtx || !isOperationalRoute(location.pathname)) {
    return null
  }

  return (
    <div className="topbar-farm">
      <label className="topbar-farm-label" htmlFor="topbar-operational-farm">
        Farm:
      </label>
      <select
        id="topbar-operational-farm"
        aria-label="Chọn Farm vận hành"
        value={farmCtx.selectedFarmId}
        disabled={farmCtx.loading}
        onChange={event => farmCtx.selectFarm(event.target.value)}
      >
        <option value="">-- Chọn Farm --</option>
        {farmCtx.farms.map(farm => (
          <option key={farm.id} value={farm.id}>
            {farm.archived_at ? `[Đã lưu trữ] ${farm.code} · ${farm.name}` : `${farm.code} · ${farm.name}`}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="btn-secondary btn-sm"
        disabled={farmCtx.loading}
        title={farmCtx.error || 'Tải lại danh sách Farm'}
        onClick={() => void farmCtx.reloadFarms()}
      >
        {farmCtx.loading ? 'Đang tải…' : 'Tải lại'}
      </button>
    </div>
  )
}
