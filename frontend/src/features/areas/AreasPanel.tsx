import { useEffect, useRef, useState } from 'react'
import { ApiError, requestJson } from '../../api/client'
import { useAuth } from '../auth/AuthProvider'

type AreaStatus = 'ACTIVE' | 'ARCHIVED' | 'ALL'
export type FarmView = {
  id: string
  code: string
  name: string
  description: string | null
  archived_at: string | null
  row_version: string
}
export type ZoneView = FarmView & { farm_id: string }
type FarmPage = { items: FarmView[]; next_cursor: string | null }
type ZonePage = { items: ZoneView[]; next_cursor: string | null }

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
  const [farmStatus, setFarmStatus] = useState<AreaStatus>('ACTIVE')
  const [farmFilter, setFarmFilter] = useState('')
  const [farms, setFarms] = useState<FarmView[]>([])
  const [farmCursor, setFarmCursor] = useState<string | null>(null)
  const [farmsLoaded, setFarmsLoaded] = useState(false)
  const [selectedFarm, setSelectedFarm] = useState<FarmView | null>(null)

  const [farmCode, setFarmCode] = useState('')
  const [farmName, setFarmName] = useState('')
  const [farmDesc, setFarmDesc] = useState('')
  const [farmUncertain, setFarmUncertain] = useState(false)

  const [zoneStatus, setZoneStatus] = useState<AreaStatus>('ACTIVE')
  const [zoneFilter, setZoneFilter] = useState('')
  const [zones, setZones] = useState<ZoneView[]>([])
  const [zoneCursor, setZoneCursor] = useState<string | null>(null)
  const [zonesLoaded, setZonesLoaded] = useState(false)
  const [selectedZone, setSelectedZone] = useState<ZoneView | null>(null)

  const [zoneCode, setZoneCode] = useState('')
  const [zoneName, setZoneName] = useState('')
  const [zoneDesc, setZoneDesc] = useState('')
  const [zoneUncertain, setZoneUncertain] = useState(false)

  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
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

  function resetZoneState() {
    zoneGen.current++
    setZones([])
    setZoneCursor(null)
    setZonesLoaded(false)
    setSelectedZone(null)
    setZoneCode('')
    setZoneName('')
    setZoneDesc('')
    setZoneUncertain(false)
  }

  async function loadFarms(more = false) {
    const active = ++farmGen.current
    setBusy(true)
    try {
      const params = new URLSearchParams({ status: farmStatus, page_size: '10' })
      if (farmFilter) params.set('code', farmFilter)
      if (more && farmCursor) params.set('cursor', farmCursor)
      const page = await requestJson<FarmPage>(`/api/v1/farms?${params}`)
      if (!mounted.current || active !== farmGen.current) return
      setFarms(prev => (more ? [...prev, ...page.items] : page.items))
      setFarmCursor(page.next_cursor)
      setFarmsLoaded(true)
      setMessage('')
    } catch (error) {
      if (!mounted.current || active !== farmGen.current) return
      setMessage(error instanceof ApiError ? error.message : 'Lỗi tải danh sách Farm.')
    } finally {
      if (mounted.current && active === farmGen.current) setBusy(false)
    }
  }

  function selectFarm(farm: FarmView) {
    setSelectedFarm(farm)
    setFarmCode(farm.code)
    setFarmName(farm.name)
    setFarmDesc(farm.description ?? '')
    setFarmUncertain(false)
    setMessage('')
    resetZoneState()
  }

  async function saveFarm() {
    if (opLock.current || !canWrite()) return
    opLock.current = true
    setBusy(true)
    const active = farmGen.current
    const isPatch = selectedFarm !== null
    const body = isPatch
      ? { name: farmName, description: farmDesc || null, expected_row_version: selectedFarm.row_version }
      : { code: farmCode, name: farmName, description: farmDesc || null }
    try {
      const saved = await requestJson<FarmView>(
        isPatch ? `/api/v1/farms/${selectedFarm.id}` : '/api/v1/farms',
        { method: isPatch ? 'PATCH' : 'POST', headers: mutationHeaders(csrf), body: JSON.stringify(body) },
      )
      if (!mounted.current || active !== farmGen.current) return
      setSelectedFarm(saved)
      setFarmCode(saved.code)
      setFarmName(saved.name)
      setFarmDesc(saved.description ?? '')
      setFarms(prev => [saved, ...prev.filter(f => f.id !== saved.id)])
      setFarmUncertain(false)
      setMessage('Đã lưu Farm và được backend xác nhận.')
    } catch (error) {
      if (!mounted.current || active !== farmGen.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setMessage(error.message)
      } else {
        setFarmUncertain(true)
        setMessage('Chưa xác nhận kết quả lưu Farm. Giữ biểu mẫu và kiểm tra lại trước khi gửi tiếp.')
      }
    } finally {
      opLock.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function toggleFarmArchive(archive: boolean) {
    if (!selectedFarm || opLock.current || !canWrite()) return
    opLock.current = true
    setBusy(true)
    const active = farmGen.current
    try {
      const updated = await requestJson<FarmView>(
        `/api/v1/farms/${selectedFarm.id}/${archive ? 'archive' : 'restore'}`,
        {
          method: 'POST',
          headers: mutationHeaders(csrf),
          body: JSON.stringify({ expected_row_version: selectedFarm.row_version }),
        },
      )
      if (!mounted.current || active !== farmGen.current) return
      setSelectedFarm(updated)
      setFarms(prev => prev.map(f => (f.id === updated.id ? updated : f)))
      setMessage(archive ? 'Đã lưu trữ Farm.' : 'Đã khôi phục Farm.')
    } catch (error) {
      if (!mounted.current || active !== farmGen.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setMessage(error.message)
      } else {
        setFarmUncertain(true)
        setMessage('Chưa xác nhận kết quả thay đổi trạng thái Farm. Hãy kiểm tra lại.')
      }
    } finally {
      opLock.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function reconcileFarm() {
    const active = farmGen.current
    setBusy(true)
    try {
      const found = selectedFarm
        ? await requestJson<FarmView>(`/api/v1/farms/${selectedFarm.id}`)
        : (await requestJson<FarmPage>(`/api/v1/farms?status=ALL&code=${encodeURIComponent(farmCode)}`)).items[0]
      if (!mounted.current || active !== farmGen.current) return
      if (found) {
        setFarms(prev => [found, ...prev.filter(f => f.id !== found.id)])
        setMessage(`Farm đang lưu: “${found.name}” (${formatViTime(found.archived_at)}). Biểu mẫu vẫn giữ nguyên.`)
      } else {
        setMessage('Chưa tìm thấy mã Farm này. Biểu mẫu vẫn giữ nguyên.')
      }
    } catch {
      if (mounted.current) setMessage('Chưa đọc lại được Farm. Hãy thử lại.')
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  async function loadZones(more = false) {
    if (!selectedFarm) return
    const active = ++zoneGen.current
    const targetFarmId = selectedFarm.id
    setBusy(true)
    try {
      const params = new URLSearchParams({ status: zoneStatus, page_size: '10' })
      if (zoneFilter) params.set('code', zoneFilter)
      if (more && zoneCursor) params.set('cursor', zoneCursor)
      const page = await requestJson<ZonePage>(`/api/v1/farms/${targetFarmId}/zones?${params}`)
      if (!mounted.current || active !== zoneGen.current) return
      setZones(prev => (more ? [...prev, ...page.items] : page.items))
      setZoneCursor(page.next_cursor)
      setZonesLoaded(true)
      setMessage('')
    } catch (error) {
      if (!mounted.current || active !== zoneGen.current) return
      setMessage(error instanceof ApiError ? error.message : 'Lỗi tải danh sách Zone.')
    } finally {
      if (mounted.current && active === zoneGen.current) setBusy(false)
    }
  }

  async function saveZone() {
    if (!selectedFarm || opLock.current || !canWrite()) return
    opLock.current = true
    setBusy(true)
    const active = zoneGen.current
    const isPatch = selectedZone !== null
    const body = isPatch
      ? { name: zoneName, description: zoneDesc || null, expected_row_version: selectedZone.row_version }
      : { code: zoneCode, name: zoneName, description: zoneDesc || null }
    try {
      const saved = await requestJson<ZoneView>(
        isPatch ? `/api/v1/zones/${selectedZone.id}` : `/api/v1/farms/${selectedFarm.id}/zones`,
        { method: isPatch ? 'PATCH' : 'POST', headers: mutationHeaders(csrf), body: JSON.stringify(body) },
      )
      if (!mounted.current || active !== zoneGen.current) return
      setSelectedZone(saved)
      setZoneCode(saved.code)
      setZoneName(saved.name)
      setZoneDesc(saved.description ?? '')
      setZones(prev => [saved, ...prev.filter(z => z.id !== saved.id)])
      setZoneUncertain(false)
      setMessage('Đã lưu Zone và được backend xác nhận.')
    } catch (error) {
      if (!mounted.current || active !== zoneGen.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setMessage(error.message)
      } else {
        setZoneUncertain(true)
        setMessage('Chưa xác nhận kết quả lưu Zone. Giữ biểu mẫu và kiểm tra lại.')
      }
    } finally {
      opLock.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function toggleZoneArchive(archive: boolean) {
    if (!selectedZone || opLock.current || !canWrite()) return
    opLock.current = true
    setBusy(true)
    const active = zoneGen.current
    try {
      const updated = await requestJson<ZoneView>(
        `/api/v1/zones/${selectedZone.id}/${archive ? 'archive' : 'restore'}`,
        {
          method: 'POST',
          headers: mutationHeaders(csrf),
          body: JSON.stringify({ expected_row_version: selectedZone.row_version }),
        },
      )
      if (!mounted.current || active !== zoneGen.current) return
      setSelectedZone(updated)
      setZones(prev => prev.map(z => (z.id === updated.id ? updated : z)))
      setMessage(archive ? 'Đã lưu trữ Zone.' : 'Đã khôi phục Zone.')
    } catch (error) {
      if (!mounted.current || active !== zoneGen.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setMessage(error.message)
      } else {
        setZoneUncertain(true)
        setMessage('Chưa xác nhận kết quả thay đổi trạng thái Zone. Hãy kiểm tra lại.')
      }
    } finally {
      opLock.current = false
      if (mounted.current) setBusy(false)
    }
  }

  return (
    <section aria-label="Quản lý Farm và Zone">
      <h2>Quản lý Farm và Zone</h2>
      <div>
        <label>
          Trạng thái Farm{' '}
          <select
            disabled={busy}
            value={farmStatus}
            onChange={e => {
              farmGen.current++
              setFarmStatus(e.target.value as AreaStatus)
              setFarms([])
              setFarmCursor(null)
              setFarmsLoaded(false)
            }}
          >
            <option value="ACTIVE">Đang hoạt động</option>
            <option value="ARCHIVED">Đã lưu trữ</option>
            <option value="ALL">Tất cả</option>
          </select>
        </label>
        <label>
          Mã Farm chính xác{' '}
          <input
            disabled={busy}
            value={farmFilter}
            onChange={e => {
              farmGen.current++
              setFarmFilter(e.target.value)
              setFarms([])
              setFarmCursor(null)
              setFarmsLoaded(false)
            }}
          />
        </label>
        <button disabled={busy} onClick={() => void loadFarms()}>
          Tải danh sách Farm
        </button>
        <button
          disabled={busy}
          onClick={() => {
            setSelectedFarm(null)
            setFarmCode('')
            setFarmName('')
            setFarmDesc('')
            setFarmUncertain(false)
            setMessage('')
            resetZoneState()
          }}
        >
          Tạo Farm mới
        </button>
      </div>

      {farmsLoaded && farms.length === 0 && (
        <p>Chưa có Farm nào khớp bộ lọc. Tạo Farm mới để bắt đầu.</p>
      )}
      <ul>
        {farms.map(f => (
          <li key={f.id}>
            <button disabled={busy} onClick={() => selectFarm(f)}>
              {f.code} · {f.name} · {formatViTime(f.archived_at)}
            </button>
          </li>
        ))}
      </ul>
      {farmCursor && (
        <button disabled={busy} onClick={() => void loadFarms(true)}>
          Tải thêm Farm
        </button>
      )}

      <form
        onSubmit={e => {
          e.preventDefault()
          void saveFarm()
        }}
      >
        <h3>{selectedFarm ? `Sửa Farm: ${selectedFarm.code}` : 'Đăng ký Farm mới'}</h3>
        <label>
          Mã Farm{' '}
          <input
            required
            disabled={busy || !!selectedFarm}
            value={farmCode}
            onChange={e => setFarmCode(e.target.value)}
          />
        </label>
        <label>
          Tên Farm{' '}
          <input
            required
            disabled={busy}
            value={farmName}
            onChange={e => setFarmName(e.target.value)}
          />
        </label>
        <label>
          Mô tả{' '}
          <textarea
            disabled={busy}
            value={farmDesc}
            onChange={e => setFarmDesc(e.target.value)}
          />
        </label>
        <button disabled={busy || !canWrite() || farmUncertain}>Lưu Farm</button>
        {selectedFarm && (
          <>
            {selectedFarm.archived_at === null ? (
              <button
                type="button"
                disabled={busy || !canWrite() || farmUncertain}
                onClick={() => void toggleFarmArchive(true)}
              >
                Lưu trữ Farm
              </button>
            ) : (
              <button
                type="button"
                disabled={busy || !canWrite() || farmUncertain}
                onClick={() => void toggleFarmArchive(false)}
              >
                Khôi phục Farm
              </button>
            )}
          </>
        )}
        <button type="button" disabled={busy || !farmCode} onClick={() => void reconcileFarm()}>
          Kiểm tra lại Farm
        </button>
      </form>

      {selectedFarm && (
        <div>
          <h3>Zone thuộc Farm {selectedFarm.code}</h3>
          <label>
            Trạng thái Zone{' '}
            <select
              disabled={busy}
              value={zoneStatus}
              onChange={e => {
                zoneGen.current++
                setZoneStatus(e.target.value as AreaStatus)
                setZones([])
                setZoneCursor(null)
                setZonesLoaded(false)
              }}
            >
              <option value="ACTIVE">Đang hoạt động</option>
              <option value="ARCHIVED">Đã lưu trữ</option>
              <option value="ALL">Tất cả</option>
            </select>
          </label>
          <label>
            Mã Zone chính xác{' '}
            <input
              disabled={busy}
              value={zoneFilter}
              onChange={e => {
                zoneGen.current++
                setZoneFilter(e.target.value)
                setZones([])
                setZoneCursor(null)
                setZonesLoaded(false)
              }}
            />
          </label>
          <button disabled={busy} onClick={() => void loadZones()}>
            Tải danh sách Zone
          </button>
          <button
            disabled={busy}
            onClick={() => {
              setSelectedZone(null)
              setZoneCode('')
              setZoneName('')
              setZoneDesc('')
              setZoneUncertain(false)
            }}
          >
            Tạo Zone mới
          </button>

          {zonesLoaded && zones.length === 0 && <p>Chưa có Zone nào trong Farm này.</p>}
          <ul>
            {zones.map(z => (
              <li key={z.id}>
                <button
                  disabled={busy}
                  onClick={() => {
                    setSelectedZone(z)
                    setZoneCode(z.code)
                    setZoneName(z.name)
                    setZoneDesc(z.description ?? '')
                    setZoneUncertain(false)
                  }}
                >
                  {z.code} · {z.name} · {formatViTime(z.archived_at)}
                </button>
              </li>
            ))}
          </ul>
          {zoneCursor && (
            <button disabled={busy} onClick={() => void loadZones(true)}>
              Tải thêm Zone
            </button>
          )}

          <form
            onSubmit={e => {
              e.preventDefault()
              void saveZone()
            }}
          >
            <h4>{selectedZone ? `Sửa Zone: ${selectedZone.code}` : 'Đăng ký Zone mới'}</h4>
            <label>
              Mã Zone{' '}
              <input
                required
                disabled={busy || !!selectedZone}
                value={zoneCode}
                onChange={e => setZoneCode(e.target.value)}
              />
            </label>
            <label>
              Tên Zone{' '}
              <input
                required
                disabled={busy}
                value={zoneName}
                onChange={e => setZoneName(e.target.value)}
              />
            </label>
            <label>
              Mô tả{' '}
              <textarea
                disabled={busy}
                value={zoneDesc}
                onChange={e => setZoneDesc(e.target.value)}
              />
            </label>
            <button disabled={busy || !canWrite() || zoneUncertain}>Lưu Zone</button>
            {selectedZone && (
              <>
                {selectedZone.archived_at === null ? (
                  <button
                    type="button"
                    disabled={busy || !canWrite() || zoneUncertain}
                    onClick={() => void toggleZoneArchive(true)}
                  >
                    Lưu trữ Zone
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={busy || !canWrite() || zoneUncertain}
                    onClick={() => void toggleZoneArchive(false)}
                  >
                    Khôi phục Zone
                  </button>
                )}
              </>
            )}
          </form>
        </div>
      )}

      <p role="status">{busy ? 'Đang xử lý…' : message}</p>
    </section>
  )
}
