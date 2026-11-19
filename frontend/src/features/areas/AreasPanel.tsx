import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { ApiError, requestJson } from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { CursorPager } from '../../components/CursorPager'
import { DataTable } from '../../components/DataTable'
import type { Column } from '../../components/DataTable'
import { Drawer } from '../../components/Drawer'
import { EmptyState } from '../../components/EmptyState'
import { FilterBar } from '../../components/FilterBar'
import { PageHeader } from '../../components/PageHeader'
import { StateBanner } from '../../components/StateBanner'
import type { BannerTone } from '../../components/StateBanner'
import { StatusBadge } from '../../components/StatusBadge'
import { useAuth } from '../auth/AuthProvider'
import type { AreaStatus, FarmPage, FarmView, ZonePage, ZoneView } from './types'

export type { AreaStatus, FarmPage, FarmView, ZonePage, ZoneView } from './types'

function formatViTime(iso: string | null): string {
  if (!iso) return 'Đang hoạt động'
  return `Đã lưu trữ (${new Date(iso).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })})`
}

function mutationHeaders(csrf: string): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'X-HQ-Client': 'web',
    'X-CSRF-Token': csrf,
  }
}

export function AreasPanel() {
  const { session } = useAuth()
  return session ? <Areas key={session.csrf_token} csrf={session.csrf_token} /> : null
}

function Areas({ csrf }: { csrf: string }) {
  const { canWrite } = useAuth()
  const farmFormId = useId()
  const zoneFormId = useId()

  // Farm list & selection state
  const [farmStatus, setFarmStatus] = useState<AreaStatus>('ACTIVE')
  const [farmFilter, setFarmFilter] = useState('')
  const [farms, setFarms] = useState<FarmView[]>([])
  const [farmCursor, setFarmCursor] = useState<string | null>(null)
  const [farmsLoaded, setFarmsLoaded] = useState(false)
  const [selectedFarm, setSelectedFarm] = useState<FarmView | null>(null)

  // Farm Drawer state (separate from selectedFarm so Create cancel keeps detail view)
  const [farmDrawerMode, setFarmDrawerMode] = useState<'create' | 'edit' | null>(null)
  const [farmDrawerTarget, setFarmDrawerTarget] = useState<FarmView | null>(null)
  const [farmCode, setFarmCode] = useState('')
  const [farmName, setFarmName] = useState('')
  const [farmDesc, setFarmDesc] = useState('')
  const [farmInitial, setFarmInitial] = useState({ code: '', name: '', desc: '' })
  const [farmUncertain, setFarmUncertain] = useState(false)
  const [farmConfirmAction, setFarmConfirmAction] = useState<{
    farm: FarmView
    archive: boolean
  } | null>(null)

  // Zone list & selection state
  const [zoneStatus, setZoneStatus] = useState<AreaStatus>('ACTIVE')
  const [zoneFilter, setZoneFilter] = useState('')
  const [zones, setZones] = useState<ZoneView[]>([])
  const [zoneCursor, setZoneCursor] = useState<string | null>(null)
  const [zonesLoaded, setZonesLoaded] = useState(false)
  const [selectedZone, setSelectedZone] = useState<ZoneView | null>(null)

  // Zone Drawer state
  const [zoneDrawerMode, setZoneDrawerMode] = useState<'create' | 'edit' | null>(null)
  const [zoneDrawerTarget, setZoneDrawerTarget] = useState<ZoneView | null>(null)
  const [zoneCode, setZoneCode] = useState('')
  const [zoneName, setZoneName] = useState('')
  const [zoneDesc, setZoneDesc] = useState('')
  const [zoneInitial, setZoneInitial] = useState({ code: '', name: '', desc: '' })
  const [zoneUncertain, setZoneUncertain] = useState(false)
  const [zoneConfirmAction, setZoneConfirmAction] = useState<{
    zone: ZoneView
    archive: boolean
  } | null>(null)

  // Status / busy / guard refs
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [messageTone, setMessageTone] = useState<BannerTone>('info')
  const [drawerConflict, setDrawerConflict] = useState(false)

  const farmGen = useRef(0)
  const zoneGen = useRef(0)
  const opLock = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      farmGen.current++
      zoneGen.current++
    }
  }, [])

  const resetZoneState = useCallback(() => {
    zoneGen.current++
    setZones([])
    setZoneCursor(null)
    setZonesLoaded(false)
    setSelectedZone(null)
    setZoneDrawerMode(null)
    setZoneDrawerTarget(null)
    setZoneConfirmAction(null)
    setZoneCode('')
    setZoneName('')
    setZoneDesc('')
    setZoneInitial({ code: '', name: '', desc: '' })
    setZoneUncertain(false)
  }, [])

  const loadFarms = useCallback(
    async (nextStatus: AreaStatus, nextFilter: string, more = false) => {
      const active = ++farmGen.current
      setBusy(true)
      try {
        const params = new URLSearchParams({ status: nextStatus, page_size: '10' })
        if (nextFilter.trim()) params.set('code', nextFilter.trim())
        if (more && farmCursor) params.set('cursor', farmCursor)
        const page = await requestJson<FarmPage>(`/api/v1/farms?${params}`)
        if (!mounted.current || active !== farmGen.current) return
        setFarms(prev => (more ? [...prev, ...page.items] : page.items))
        setFarmCursor(page.next_cursor)
        setFarmsLoaded(true)
        if (!more) {
          setMessage('')
        }
      } catch (error) {
        if (!mounted.current || active !== farmGen.current) return
        setMessageTone('err')
        setMessage(error instanceof ApiError ? error.message : 'Lỗi tải danh sách Farm.')
      } finally {
        if (mounted.current && active === farmGen.current) setBusy(false)
      }
    },
    [farmCursor],
  )

  const loadZones = useCallback(
    async (targetFarmId: string, nextStatus: AreaStatus, nextFilter: string, more = false) => {
      const active = ++zoneGen.current
      setBusy(true)
      try {
        const params = new URLSearchParams({ status: nextStatus, page_size: '10' })
        if (nextFilter.trim()) params.set('code', nextFilter.trim())
        if (more && zoneCursor) params.set('cursor', zoneCursor)
        const page = await requestJson<ZonePage>(`/api/v1/farms/${targetFarmId}/zones?${params}`)
        if (!mounted.current || active !== zoneGen.current) return
        setZones(prev => (more ? [...prev, ...page.items] : page.items))
        setZoneCursor(page.next_cursor)
        setZonesLoaded(true)
        if (!more) {
          setMessage('')
        }
      } catch (error) {
        if (!mounted.current || active !== zoneGen.current) return
        setMessageTone('err')
        setMessage(error instanceof ApiError ? error.message : 'Lỗi tải danh sách Zone.')
      } finally {
        if (mounted.current && active === zoneGen.current) setBusy(false)
      }
    },
    [zoneCursor],
  )

  // Initial Farm list load when authenticated view mounts
  useEffect(() => {
    void loadFarms('ACTIVE', '', false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function selectFarm(farm: FarmView) {
    setSelectedFarm(farm)
    setFarmUncertain(false)
    setMessage('')
    resetZoneState()
    void loadZones(farm.id, zoneStatus, zoneFilter, false)
  }

  function openCreateFarmDrawer() {
    setFarmDrawerMode('create')
    setFarmDrawerTarget(null)
    setFarmCode('')
    setFarmName('')
    setFarmDesc('')
    setFarmInitial({ code: '', name: '', desc: '' })
    setFarmUncertain(false)
    setDrawerConflict(false)
    setMessage('')
  }

  function openEditFarmDrawer(farm: FarmView) {
    const desc = farm.description ?? ''
    setSelectedFarm(farm)
    setFarmDrawerMode('edit')
    setFarmDrawerTarget(farm)
    setFarmCode(farm.code)
    setFarmName(farm.name)
    setFarmDesc(desc)
    setFarmInitial({ code: farm.code, name: farm.name, desc })
    setFarmUncertain(false)
    setDrawerConflict(false)
    setMessage('')
  }

  async function saveFarm() {
    if (opLock.current || !canWrite()) return
    const isPatch = farmDrawerMode === 'edit' && farmDrawerTarget !== null
    opLock.current = true
    setBusy(true)
    setDrawerConflict(false)
    const active = farmGen.current
    const body = isPatch
      ? {
          name: farmName,
          description: farmDesc || null,
          expected_row_version: farmDrawerTarget.row_version,
        }
      : { code: farmCode, name: farmName, description: farmDesc || null }
    try {
      const saved = await requestJson<FarmView>(
        isPatch ? `/api/v1/farms/${farmDrawerTarget.id}` : '/api/v1/farms',
        { method: isPatch ? 'PATCH' : 'POST', headers: mutationHeaders(csrf), body: JSON.stringify(body) },
      )
      if (!mounted.current || active !== farmGen.current) return
      const isSelectedSame = selectedFarm?.id === saved.id
      setSelectedFarm(saved)
      setFarms(prev => [saved, ...prev.filter(f => f.id !== saved.id)])
      setFarmUncertain(false)
      setDrawerConflict(false)
      setFarmDrawerMode(null)
      setFarmDrawerTarget(null)
      setMessageTone('ok')
      setMessage('Đã lưu Farm và được backend xác nhận.')
      if (!isPatch || !isSelectedSame) {
        resetZoneState()
        void loadZones(saved.id, zoneStatus, zoneFilter, false)
      }
    } catch (error) {
      if (!mounted.current || active !== farmGen.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setMessageTone('err')
        setMessage(error.message)
        if (error.status === 409) {
          setDrawerConflict(true)
        }
      } else {
        setFarmUncertain(true)
        setMessageTone('warn')
        setMessage('Chưa xác nhận kết quả lưu Farm. Giữ biểu mẫu và kiểm tra lại trước khi gửi tiếp.')
      }
    } finally {
      opLock.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function toggleFarmArchive(farm: FarmView, archive: boolean) {
    if (opLock.current || !canWrite()) return
    opLock.current = true
    setBusy(true)
    const active = farmGen.current
    try {
      const updated = await requestJson<FarmView>(
        `/api/v1/farms/${farm.id}/${archive ? 'archive' : 'restore'}`,
        {
          method: 'POST',
          headers: mutationHeaders(csrf),
          body: JSON.stringify({ expected_row_version: farm.row_version }),
        },
      )
      if (!mounted.current || active !== farmGen.current) return
      if (selectedFarm?.id === updated.id) {
        setSelectedFarm(updated)
      }
      setFarms(prev => prev.map(f => (f.id === updated.id ? updated : f)))
      setFarmUncertain(false)
      setFarmConfirmAction(null)
      setMessageTone('ok')
      setMessage(archive ? 'Đã lưu trữ Farm.' : 'Đã khôi phục Farm.')
    } catch (error) {
      if (!mounted.current || active !== farmGen.current) return
      setFarmConfirmAction(null)
      if (error instanceof ApiError && error.status !== 503) {
        setMessageTone('err')
        setMessage(
          error.code === 'AREA_IN_USE'
            ? `${error.message} (Quy tắc leaf-first: cần tắt quyền thiết bị và lưu trữ Zone/Gateway con trước.)`
            : error.message,
        )
      } else {
        setFarmUncertain(true)
        setMessageTone('warn')
        setMessage('Chưa xác nhận kết quả thay đổi trạng thái Farm. Hãy kiểm tra lại.')
      }
    } finally {
      opLock.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function reconcileFarm(reloadLatestIntoForm = false) {
    const active = farmGen.current
    setBusy(true)
    try {
      const targetId = farmDrawerTarget?.id ?? selectedFarm?.id
      const found = targetId
        ? await requestJson<FarmView>(`/api/v1/farms/${targetId}`)
        : (
            await requestJson<FarmPage>(
              `/api/v1/farms?status=ALL&code=${encodeURIComponent(farmCode)}`,
            )
          ).items[0]
      if (!mounted.current || active !== farmGen.current) return
      if (found) {
        setFarms(prev => [found, ...prev.filter(f => f.id !== found.id)])
        if (selectedFarm?.id === found.id) {
          setSelectedFarm(found)
        }
        if (reloadLatestIntoForm) {
          const desc = found.description ?? ''
          setFarmDrawerTarget(found)
          setFarmCode(found.code)
          setFarmName(found.name)
          setFarmDesc(desc)
          setFarmInitial({ code: found.code, name: found.name, desc })
          setFarmUncertain(false)
          setDrawerConflict(false)
          setMessageTone('info')
          setMessage(`Đã nạp bản mới nhất của Farm ${found.code} vào biểu mẫu.`)
        } else {
          setMessageTone('info')
          setMessage(
            `Farm đang lưu: “${found.name}” (${formatViTime(found.archived_at)}). Biểu mẫu vẫn giữ nguyên.`,
          )
        }
      } else {
        setMessageTone('info')
        setMessage('Chưa tìm thấy mã Farm này. Biểu mẫu vẫn giữ nguyên.')
      }
    } catch {
      if (mounted.current) {
        setMessageTone('err')
        setMessage('Chưa đọc lại được Farm. Hãy thử lại.')
      }
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  function openCreateZoneDrawer() {
    if (!selectedFarm) return
    setZoneDrawerMode('create')
    setZoneDrawerTarget(null)
    setZoneCode('')
    setZoneName('')
    setZoneDesc('')
    setZoneInitial({ code: '', name: '', desc: '' })
    setZoneUncertain(false)
    setDrawerConflict(false)
    setMessage('')
  }

  function openEditZoneDrawer(zone: ZoneView) {
    const desc = zone.description ?? ''
    setSelectedZone(zone)
    setZoneDrawerMode('edit')
    setZoneDrawerTarget(zone)
    setZoneCode(zone.code)
    setZoneName(zone.name)
    setZoneDesc(desc)
    setZoneInitial({ code: zone.code, name: zone.name, desc })
    setZoneUncertain(false)
    setDrawerConflict(false)
    setMessage('')
  }

  async function saveZone() {
    if (!selectedFarm || opLock.current || !canWrite()) return
    const isPatch = zoneDrawerMode === 'edit' && zoneDrawerTarget !== null
    opLock.current = true
    setBusy(true)
    setDrawerConflict(false)
    const active = zoneGen.current
    const body = isPatch
      ? {
          name: zoneName,
          description: zoneDesc || null,
          expected_row_version: zoneDrawerTarget.row_version,
        }
      : { code: zoneCode, name: zoneName, description: zoneDesc || null }
    try {
      const saved = await requestJson<ZoneView>(
        isPatch ? `/api/v1/zones/${zoneDrawerTarget.id}` : `/api/v1/farms/${selectedFarm.id}/zones`,
        { method: isPatch ? 'PATCH' : 'POST', headers: mutationHeaders(csrf), body: JSON.stringify(body) },
      )
      if (!mounted.current || active !== zoneGen.current) return
      setSelectedZone(saved)
      setZones(prev => [saved, ...prev.filter(z => z.id !== saved.id)])
      setZoneUncertain(false)
      setDrawerConflict(false)
      setZoneDrawerMode(null)
      setZoneDrawerTarget(null)
      setMessageTone('ok')
      setMessage('Đã lưu Zone và được backend xác nhận.')
    } catch (error) {
      if (!mounted.current || active !== zoneGen.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setMessageTone('err')
        setMessage(error.message)
        if (error.status === 409) {
          setDrawerConflict(true)
        }
      } else {
        setZoneUncertain(true)
        setMessageTone('warn')
        setMessage('Chưa xác nhận kết quả lưu Zone. Giữ biểu mẫu và kiểm tra lại.')
      }
    } finally {
      opLock.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function toggleZoneArchive(zone: ZoneView, archive: boolean) {
    if (opLock.current || !canWrite()) return
    opLock.current = true
    setBusy(true)
    const active = zoneGen.current
    try {
      const updated = await requestJson<ZoneView>(
        `/api/v1/zones/${zone.id}/${archive ? 'archive' : 'restore'}`,
        {
          method: 'POST',
          headers: mutationHeaders(csrf),
          body: JSON.stringify({ expected_row_version: zone.row_version }),
        },
      )
      if (!mounted.current || active !== zoneGen.current) return
      if (selectedZone?.id === updated.id) {
        setSelectedZone(updated)
      }
      setZones(prev => prev.map(z => (z.id === updated.id ? updated : z)))
      setZoneUncertain(false)
      setZoneConfirmAction(null)
      setMessageTone('ok')
      setMessage(archive ? 'Đã lưu trữ Zone.' : 'Đã khôi phục Zone.')
    } catch (error) {
      if (!mounted.current || active !== zoneGen.current) return
      setZoneConfirmAction(null)
      if (error instanceof ApiError && error.status !== 503) {
        setMessageTone('err')
        setMessage(
          error.code === 'AREA_IN_USE'
            ? `${error.message} (Quy tắc leaf-first: cần tắt quyền các Node thuộc Zone trước.)`
            : error.message,
        )
      } else {
        setZoneUncertain(true)
        setMessageTone('warn')
        setMessage('Chưa xác nhận kết quả thay đổi trạng thái Zone. Hãy kiểm tra lại.')
      }
    } finally {
      opLock.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function reconcileZone(reloadLatestIntoForm = false) {
    if (!selectedFarm) return
    const active = zoneGen.current
    setBusy(true)
    try {
      const targetId = zoneDrawerTarget?.id ?? selectedZone?.id
      const found = targetId
        ? await requestJson<ZoneView>(`/api/v1/zones/${targetId}`)
        : (
            await requestJson<ZonePage>(
              `/api/v1/farms/${selectedFarm.id}/zones?status=ALL&code=${encodeURIComponent(zoneCode)}`,
            )
          ).items[0]
      if (!mounted.current || active !== zoneGen.current) return
      if (found) {
        setZones(prev => [found, ...prev.filter(z => z.id !== found.id)])
        if (selectedZone?.id === found.id) {
          setSelectedZone(found)
        }
        if (reloadLatestIntoForm) {
          const desc = found.description ?? ''
          setZoneDrawerTarget(found)
          setZoneCode(found.code)
          setZoneName(found.name)
          setZoneDesc(desc)
          setZoneInitial({ code: found.code, name: found.name, desc })
          setZoneUncertain(false)
          setDrawerConflict(false)
          setMessageTone('info')
          setMessage(`Đã nạp bản mới nhất của Zone ${found.code} vào biểu mẫu.`)
        } else {
          setMessageTone('info')
          setMessage(
            `Zone đang lưu: “${found.name}” (${formatViTime(found.archived_at)}). Biểu mẫu vẫn giữ nguyên.`,
          )
        }
      } else {
        setMessageTone('info')
        setMessage('Chưa tìm thấy mã Zone này. Biểu mẫu vẫn giữ nguyên.')
      }
    } catch {
      if (mounted.current) {
        setMessageTone('err')
        setMessage('Chưa đọc lại được Zone. Hãy thử lại.')
      }
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const farmDirty =
    farmDrawerMode !== null &&
    (farmCode !== farmInitial.code || farmName !== farmInitial.name || farmDesc !== farmInitial.desc)

  const zoneDirty =
    zoneDrawerMode !== null &&
    (zoneCode !== zoneInitial.code || zoneName !== zoneInitial.name || zoneDesc !== zoneInitial.desc)

  const farmColumns: Column<FarmView>[] = [
    {
      key: 'code',
      header: 'Mã Farm',
      render: item => <span className="mono">{item.code}</span>,
    },
    {
      key: 'name',
      header: 'Tên Farm',
      render: item => <strong>{item.name}</strong>,
    },
    {
      key: 'status',
      header: 'Trạng thái',
      render: item => (
        <StatusBadge
          tone={item.archived_at === null ? 'ok' : 'neutral'}
          label={item.archived_at === null ? 'Đang hoạt động' : 'Đã lưu trữ'}
        />
      ),
    },
    {
      key: 'actions',
      header: 'Thao tác',
      render: item => (
        <div className="row">
          <button
            type="button"
            className={selectedFarm?.id === item.id ? 'btn-sm' : 'btn-secondary btn-sm'}
            disabled={busy}
            onClick={() => selectFarm(item)}
          >
            {selectedFarm?.id === item.id ? 'Đang chọn' : 'Chọn'}
          </button>
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={busy}
            onClick={() => openEditFarmDrawer(item)}
          >
            Sửa
          </button>
        </div>
      ),
    },
  ]

  const zoneColumns: Column<ZoneView>[] = [
    {
      key: 'code',
      header: 'Mã Zone',
      render: item => <span className="mono">{item.code}</span>,
    },
    {
      key: 'name',
      header: 'Tên Zone',
      render: item => <strong>{item.name}</strong>,
    },
    {
      key: 'status',
      header: 'Trạng thái',
      render: item => (
        <StatusBadge
          tone={item.archived_at === null ? 'ok' : 'neutral'}
          label={item.archived_at === null ? 'Đang hoạt động' : 'Đã lưu trữ'}
        />
      ),
    },
    {
      key: 'actions',
      header: 'Thao tác',
      render: item => (
        <div className="row">
          <button
            type="button"
            className="btn-secondary btn-sm"
            disabled={busy}
            onClick={() => openEditZoneDrawer(item)}
          >
            Sửa
          </button>
          {item.archived_at === null ? (
            <button
              type="button"
              className="btn-ghost btn-sm"
              disabled={busy || !canWrite() || zoneUncertain}
              onClick={() => setZoneConfirmAction({ zone: item, archive: true })}
            >
              Lưu trữ
            </button>
          ) : (
            <button
              type="button"
              className="btn-ghost btn-sm"
              disabled={busy || !canWrite() || zoneUncertain}
              onClick={() => setZoneConfirmAction({ zone: item, archive: false })}
            >
              Khôi phục
            </button>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="stack" aria-label="Quản lý Farm và Zone">
      <PageHeader
        eyebrow="Quản trị hệ thống · FL-02 (G1)"
        title="Quản lý Farm & Zone"
        description="Quản lý toàn bộ danh sách Farm và các Zone trực thuộc từ SQL Server (độc lập với bộ chọn Farm vận hành)."
        actions={
          <button type="button" disabled={busy || !canWrite()} onClick={openCreateFarmDrawer}>
            + Tạo Farm mới
          </button>
        }
      />

      {(busy || message) && (
        <StateBanner
          tone={busy ? 'info' : messageTone}
          message={busy ? 'Đang xử lý yêu cầu với Backend…' : message}
          action={
            farmUncertain && (farmCode || selectedFarm) ? (
              <button
                type="button"
                className="btn-secondary btn-sm"
                disabled={busy}
                onClick={() => void reconcileFarm(false)}
              >
                Kiểm tra lại Farm
              </button>
            ) : zoneUncertain && selectedFarm && (zoneCode || selectedZone) ? (
              <button
                type="button"
                className="btn-secondary btn-sm"
                disabled={busy}
                onClick={() => void reconcileZone(false)}
              >
                Kiểm tra lại Zone
              </button>
            ) : undefined
          }
        />
      )}

      <div className="master-detail">
        {/* Cột trái: Danh sách Farm */}
        <section className="card stack" aria-label="Danh sách Farm">
          <div className="card-header">
            <h2>Danh sách Farm</h2>
          </div>

          <form
            onSubmit={event => {
              event.preventDefault()
              void loadFarms(farmStatus, farmFilter, false)
            }}
          >
            <FilterBar>
              <label className="filter-field">
                Trạng thái Farm
                <select
                  disabled={busy}
                  value={farmStatus}
                  onChange={event => {
                    const nextStatus = event.target.value as AreaStatus
                    farmGen.current++
                    setFarmStatus(nextStatus)
                    setFarms([])
                    setFarmCursor(null)
                    setFarmsLoaded(false)
                    void loadFarms(nextStatus, farmFilter, false)
                  }}
                >
                  <option value="ACTIVE">Đang hoạt động</option>
                  <option value="ARCHIVED">Đã lưu trữ</option>
                  <option value="ALL">Tất cả</option>
                </select>
              </label>

              <label className="filter-field">
                Mã Farm chính xác
                <input
                  disabled={busy}
                  placeholder="Ví dụ: FARM-01"
                  value={farmFilter}
                  onChange={event => {
                    farmGen.current++
                    setFarmFilter(event.target.value)
                    setFarms([])
                    setFarmCursor(null)
                    setFarmsLoaded(false)
                  }}
                />
              </label>

              <div className="filter-actions">
                <button type="submit" disabled={busy}>
                  Tải danh sách
                </button>
                {farmFilter && (
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busy}
                    onClick={() => {
                      setFarmFilter('')
                      void loadFarms(farmStatus, '', false)
                    }}
                  >
                    Đặt lại
                  </button>
                )}
              </div>
            </FilterBar>
          </form>

          <DataTable
            columns={farmColumns}
            items={farms}
            getKey={f => f.id}
            selectedKey={selectedFarm?.id ?? null}
            loading={busy}
            loaded={farmsLoaded}
            emptyTitle="Chưa có Farm nào khớp bộ lọc"
            emptyDescription="Thay đổi bộ lọc trạng thái hoặc nhấn Tạo Farm mới để bắt đầu."
          />

          {farmsLoaded && (
            <CursorPager
              count={farms.length}
              nextCursor={farmCursor}
              busy={busy}
              onLoadMore={() => void loadFarms(farmStatus, farmFilter, true)}
            />
          )}
        </section>

        {/* Cột phải: Chi tiết Farm đang chọn & Danh sách Zone */}
        <section className="card stack" aria-label="Chi tiết Farm và Zone">
          {!selectedFarm ? (
            <EmptyState
              title="Chưa chọn Farm"
              description="Chọn một Farm ở danh sách bên trái để xem thông tin chi tiết và quản lý các Zone trực thuộc."
            />
          ) : (
            <>
              <div className="card-header">
                <div className="stack-sm">
                  <div className="row">
                    <h2>
                      <span className="mono">{selectedFarm.code}</span> · {selectedFarm.name}
                    </h2>
                    <StatusBadge
                      tone={selectedFarm.archived_at === null ? 'ok' : 'neutral'}
                      label={selectedFarm.archived_at === null ? 'Đang hoạt động' : 'Đã lưu trữ'}
                    />
                  </div>
                  <small>{formatViTime(selectedFarm.archived_at)}</small>
                </div>

                <div className="row">
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    disabled={busy}
                    onClick={() => openEditFarmDrawer(selectedFarm)}
                  >
                    Sửa Farm
                  </button>
                  {selectedFarm.archived_at === null ? (
                    <button
                      type="button"
                      className="btn-secondary btn-sm"
                      disabled={busy || !canWrite() || farmUncertain}
                      onClick={() => setFarmConfirmAction({ farm: selectedFarm, archive: true })}
                    >
                      Lưu trữ Farm
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn-secondary btn-sm"
                      disabled={busy || !canWrite() || farmUncertain}
                      onClick={() => setFarmConfirmAction({ farm: selectedFarm, archive: false })}
                    >
                      Khôi phục Farm
                    </button>
                  )}
                </div>
              </div>

              <dl className="kv-grid">
                <div className="kv-item">
                  <dt>ID nội bộ</dt>
                  <dd className="mono">{selectedFarm.id}</dd>
                </div>
                <div className="kv-item">
                  <dt>Mô tả</dt>
                  <dd>{selectedFarm.description || 'Không có mô tả'}</dd>
                </div>
              </dl>

              <hr style={{ border: 0, borderTop: '1px solid var(--color-border)', margin: 0 }} />

              <div className="card-header">
                <h3>Zone thuộc Farm {selectedFarm.code}</h3>
                <button
                  type="button"
                  className="btn-sm"
                  disabled={busy || !canWrite()}
                  onClick={openCreateZoneDrawer}
                >
                  + Tạo Zone mới
                </button>
              </div>

              <form
                onSubmit={event => {
                  event.preventDefault()
                  void loadZones(selectedFarm.id, zoneStatus, zoneFilter, false)
                }}
              >
                <FilterBar>
                  <label className="filter-field">
                    Trạng thái Zone
                    <select
                      disabled={busy}
                      value={zoneStatus}
                      onChange={event => {
                        const nextStatus = event.target.value as AreaStatus
                        zoneGen.current++
                        setZoneStatus(nextStatus)
                        setZones([])
                        setZoneCursor(null)
                        setZonesLoaded(false)
                        void loadZones(selectedFarm.id, nextStatus, zoneFilter, false)
                      }}
                    >
                      <option value="ACTIVE">Đang hoạt động</option>
                      <option value="ARCHIVED">Đã lưu trữ</option>
                      <option value="ALL">Tất cả</option>
                    </select>
                  </label>

                  <label className="filter-field">
                    Mã Zone chính xác
                    <input
                      disabled={busy}
                      placeholder="Ví dụ: ZONE-1"
                      value={zoneFilter}
                      onChange={event => {
                        zoneGen.current++
                        setZoneFilter(event.target.value)
                        setZones([])
                        setZoneCursor(null)
                        setZonesLoaded(false)
                      }}
                    />
                  </label>

                  <div className="filter-actions">
                    <button type="submit" className="btn-secondary" disabled={busy}>
                      Tải danh sách Zone
                    </button>
                  </div>
                </FilterBar>
              </form>

              <DataTable
                columns={zoneColumns}
                items={zones}
                getKey={z => z.id}
                selectedKey={selectedZone?.id ?? null}
                loading={busy}
                loaded={zonesLoaded}
                emptyTitle="Chưa có Zone nào trong Farm này"
                emptyDescription="Nhấn Tạo Zone mới để thêm khu vực con cho Farm."
              />

              {zonesLoaded && (
                <CursorPager
                  count={zones.length}
                  nextCursor={zoneCursor}
                  busy={busy}
                  onLoadMore={() => void loadZones(selectedFarm.id, zoneStatus, zoneFilter, true)}
                />
              )}
            </>
          )}
        </section>
      </div>

      {/* Drawer Tạo / Sửa Farm */}
      <Drawer
        open={farmDrawerMode !== null}
        title={
          farmDrawerMode === 'edit' && farmDrawerTarget
            ? `Sửa Farm: ${farmDrawerTarget.code}`
            : 'Đăng ký Farm mới'
        }
        onClose={() => {
          setFarmDrawerMode(null)
          setFarmDrawerTarget(null)
          setDrawerConflict(false)
        }}
        busy={busy}
        dirty={farmDirty}
        footer={requestClose => (
          <>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={requestClose}
            >
              Hủy
            </button>
            <button
              type="submit"
              form={farmFormId}
              disabled={busy || !canWrite() || farmUncertain}
            >
              {busy ? 'Đang lưu…' : 'Lưu Farm'}
            </button>
          </>
        )}
      >
        {(message || farmUncertain || drawerConflict) && (
          <StateBanner
            tone={farmUncertain ? 'warn' : messageTone}
            message={
              message ||
              'Chưa xác nhận kết quả lưu Farm. Giữ biểu mẫu và kiểm tra lại trước khi gửi tiếp.'
            }
            action={
              (drawerConflict || farmUncertain) && (farmCode || farmDrawerTarget) ? (
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  disabled={busy}
                  onClick={() => void reconcileFarm(drawerConflict)}
                >
                  {drawerConflict ? 'Tải lại bản mới nhất' : 'Kiểm tra lại Farm'}
                </button>
              ) : undefined
            }
          />
        )}

        <form
          id={farmFormId}
          className="stack"
          onSubmit={event => {
            event.preventDefault()
            void saveFarm()
          }}
        >
          <label>
            Mã Farm
            <input
              required
              disabled={busy || farmDrawerMode === 'edit'}
              value={farmCode}
              onChange={event => setFarmCode(event.target.value)}
            />
          </label>
          <label>
            Tên Farm
            <input
              required
              disabled={busy}
              value={farmName}
              onChange={event => setFarmName(event.target.value)}
            />
          </label>
          <label>
            Mô tả
            <textarea
              disabled={busy}
              value={farmDesc}
              onChange={event => setFarmDesc(event.target.value)}
            />
          </label>
        </form>
      </Drawer>

      {/* Drawer Tạo / Sửa Zone */}
      <Drawer
        open={zoneDrawerMode !== null}
        title={
          zoneDrawerMode === 'edit' && zoneDrawerTarget
            ? `Sửa Zone: ${zoneDrawerTarget.code}`
            : `Đăng ký Zone mới (${selectedFarm?.code ?? ''})`
        }
        onClose={() => {
          setZoneDrawerMode(null)
          setZoneDrawerTarget(null)
          setDrawerConflict(false)
        }}
        busy={busy}
        dirty={zoneDirty}
        footer={requestClose => (
          <>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={requestClose}
            >
              Hủy
            </button>
            <button
              type="submit"
              form={zoneFormId}
              disabled={busy || !canWrite() || zoneUncertain}
            >
              {busy ? 'Đang lưu…' : 'Lưu Zone'}
            </button>
          </>
        )}
      >
        {(message || zoneUncertain || drawerConflict) && (
          <StateBanner
            tone={zoneUncertain ? 'warn' : messageTone}
            message={
              message ||
              'Chưa xác nhận kết quả lưu Zone. Giữ biểu mẫu và kiểm tra lại trước khi gửi tiếp.'
            }
            action={
              (drawerConflict || zoneUncertain) && selectedFarm && (zoneCode || zoneDrawerTarget) ? (
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  disabled={busy}
                  onClick={() => void reconcileZone(drawerConflict)}
                >
                  {drawerConflict ? 'Tải lại bản mới nhất' : 'Kiểm tra lại Zone'}
                </button>
              ) : undefined
            }
          />
        )}

        <form
          id={zoneFormId}
          className="stack"
          onSubmit={event => {
            event.preventDefault()
            void saveZone()
          }}
        >
          <label>
            Mã Zone
            <input
              required
              disabled={busy || zoneDrawerMode === 'edit'}
              value={zoneCode}
              onChange={event => setZoneCode(event.target.value)}
            />
          </label>
          <label>
            Tên Zone
            <input
              required
              disabled={busy}
              value={zoneName}
              onChange={event => setZoneName(event.target.value)}
            />
          </label>
          <label>
            Mô tả
            <textarea
              disabled={busy}
              value={zoneDesc}
              onChange={event => setZoneDesc(event.target.value)}
            />
          </label>
        </form>
      </Drawer>

      {/* Modal xác nhận Lưu trữ / Khôi phục Farm */}
      <ConfirmDialog
        open={farmConfirmAction !== null}
        title={
          farmConfirmAction?.archive
            ? `Lưu trữ Farm ${farmConfirmAction.farm.code}`
            : `Khôi phục Farm ${farmConfirmAction?.farm.code ?? ''}`
        }
        description={
          farmConfirmAction?.archive ? (
            <div className="stack-sm">
              <p>
                Bạn có chắc muốn lưu trữ Farm <strong>{farmConfirmAction.farm.name}</strong>?
              </p>
              <small>
                Quy tắc leaf-first: hệ thống sẽ từ chối (409 AREA_IN_USE) nếu còn Zone đang hoạt
                động, Gateway/Node đang bật quyền tiếp nhận hoặc thiết bị liên kết chưa xử lý.
              </small>
            </div>
          ) : (
            <p>
              Khôi phục Farm <strong>{farmConfirmAction?.farm.name}</strong> về trạng thái Đang
              hoạt động? (Thao tác này không tự bật lại quyền tiếp nhận hay Zone con.)
            </p>
          )
        }
        confirmLabel={farmConfirmAction?.archive ? 'Lưu trữ Farm' : 'Khôi phục Farm'}
        tone={farmConfirmAction?.archive ? 'danger' : 'primary'}
        busy={busy}
        onConfirm={() => {
          if (farmConfirmAction) {
            void toggleFarmArchive(farmConfirmAction.farm, farmConfirmAction.archive)
          }
        }}
        onCancel={() => setFarmConfirmAction(null)}
      />

      {/* Modal xác nhận Lưu trữ / Khôi phục Zone */}
      <ConfirmDialog
        open={zoneConfirmAction !== null}
        title={
          zoneConfirmAction?.archive
            ? `Lưu trữ Zone ${zoneConfirmAction.zone.code}`
            : `Khôi phục Zone ${zoneConfirmAction?.zone.code ?? ''}`
        }
        description={
          zoneConfirmAction?.archive ? (
            <div className="stack-sm">
              <p>
                Bạn có chắc muốn lưu trữ Zone <strong>{zoneConfirmAction.zone.name}</strong>?
              </p>
              <small>
                Quy tắc leaf-first: hệ thống sẽ từ chối (409 AREA_IN_USE) nếu còn Node đang bật
                quyền tiếp nhận trong Zone này.
              </small>
            </div>
          ) : (
            <p>
              Khôi phục Zone <strong>{zoneConfirmAction?.zone.name}</strong> về trạng thái Đang
              hoạt động? (Yêu cầu Farm cha đang hoạt động; không tự bật lại quyền Node.)
            </p>
          )
        }
        confirmLabel={zoneConfirmAction?.archive ? 'Lưu trữ Zone' : 'Khôi phục Zone'}
        tone={zoneConfirmAction?.archive ? 'danger' : 'primary'}
        busy={busy}
        onConfirm={() => {
          if (zoneConfirmAction) {
            void toggleZoneArchive(zoneConfirmAction.zone, zoneConfirmAction.archive)
          }
        }}
        onCancel={() => setZoneConfirmAction(null)}
      />
    </div>
  )
}
