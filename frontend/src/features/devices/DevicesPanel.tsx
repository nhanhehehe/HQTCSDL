import { useEffect, useRef, useState } from 'react'
import { ApiError, requestJson } from '../../api/client'
import { useAuth } from '../auth/AuthProvider'
import type { FarmView, ZoneView } from '../areas/AreasPanel'
import { FarmZoneLoader } from '../areas/loader'

type Profile = {
  id: string
  device_id?: string
  gateway_id?: string
  name: string
  notes: string | null
  row_version: string
  receiving_enabled: boolean
  last_new_persisted_at: string | null
  gateway?: { code: string } | null
  zone?: { id: string; code: string; name: string }
  farm?: { id: string; code: string; name: string }
  nodes?: { id: string; device_id: string; name: string }[]
  metrics?: { metric_code: string; unit: string }[]
}
type Page = { items: Profile[]; next_cursor: string | null }
type CredentialView = {
  id: string
  gateway_id: string
  created_at: string
  revoked_at: string | null
  status: 'ACTIVE' | 'REVOKED'
}
type CredentialPage = { items: CredentialView[]; next_cursor: string | null }
type CredentialIssueResponse = { credential: CredentialView; api_key: string }
type MetricDef = {
  code: string
  unit: string
  min_value: string
  max_value: string
  decimal_places: number
}

function mutationHeaders(csrf: string): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'X-HQ-Client': 'web',
    'X-CSRF-Token': csrf,
  }
}

export function DevicesPanel() {
  const { session } = useAuth()
  return session ? <Devices key={session.csrf_token} csrf={session.csrf_token} /> : null
}

function Devices({ csrf }: { csrf: string }) {
  const { canWrite } = useAuth()
  const [farms, setFarms] = useState<FarmView[]>([])
  const [selectedFarmId, setSelectedFarmId] = useState('')
  const [zones, setZones] = useState<ZoneView[]>([])
  const [metricsCatalog, setMetricsCatalog] = useState<MetricDef[]>([])

  const [kind, setKind] = useState<'nodes' | 'gateways'>('nodes')
  const [items, setItems] = useState<Profile[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [selected, setSelected] = useState<Profile | null>(null)
  const [filter, setFilter] = useState('')
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [notes, setNotes] = useState('')
  const [zone, setZone] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [uncertain, setUncertain] = useState(false)

  const [creds, setCreds] = useState<CredentialView[]>([])
  const [credCursor, setCredCursor] = useState<string | null>(null)
  const [credLoaded, setCredLoaded] = useState(false)
  const [issuedKey, setIssuedKey] = useState<string | null>(null)
  const [credUncertain, setCredUncertain] = useState(false)
  const [credMessage, setCredMessage] = useState('')

  const generation = useRef(0)
  const areaLoader = useRef(new FarmZoneLoader())
  const credGen = useRef(0)
  const operation = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    const loader = areaLoader.current
    return () => {
      mounted.current = false
      generation.current++
      loader.dispose()
      credGen.current++
      setIssuedKey(null)
    }
  }, [])

  async function refreshAreasAndMetrics(signal?: AbortSignal) {
    try {
      const [allFarms, metricsRes] = await Promise.all([
        areaLoader.current.loadActiveFarms(signal),
        requestJson<{ items: MetricDef[] }>('/api/v1/metrics', { signal }),
      ])
      if (!mounted.current || signal?.aborted) return
      setFarms(allFarms)
      setMetricsCatalog(metricsRes.items)
    } catch {
      if (!signal?.aborted && mounted.current) {
        setMessage('Không tải được danh sách Farm hoặc danh mục metric.')
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    void refreshAreasAndMetrics(controller.signal)
    return () => controller.abort()
  }, [])

  function handleFarmChange(nextFarmId: string) {
    void areaLoader.current.selectFarm(nextFarmId, {
      onReset: farmId => {
        setSelectedFarmId(farmId)
        setZones([])
        setZone('')
      },
      onLoaded: allZones => {
        if (mounted.current) setZones(allZones)
      },
      onError: () => {
        if (mounted.current) setMessage('Không tải được Zone thuộc Farm đã chọn.')
      },
    })
  }

  useEffect(() => {
    generation.current++
    credGen.current++
    setItems([])
    setCursor(null)
    setSelected(null)
    setLoaded(false)
    setCode('')
    setName('')
    setNotes('')
    setUncertain(false)
    setMessage('')
    setCreds([])
    setCredCursor(null)
    setCredLoaded(false)
    setIssuedKey(null)
    setCredUncertain(false)
    setCredMessage('')
  }, [kind])

  async function load(more = false) {
    const active = ++generation.current
    setBusy(true)
    try {
      const params = new URLSearchParams({ page_size: '10' })
      if (filter) params.set(kind === 'nodes' ? 'device_id' : 'gateway_id', filter)
      if (more && cursor) params.set('cursor', cursor)
      const value = await requestJson<Page>(`/api/v1/${kind}?${params}`)
      if (active !== generation.current || !mounted.current) return
      setItems(previous => (more ? [...previous, ...value.items] : value.items))
      setCursor(value.next_cursor)
      setLoaded(true)
      setMessage('')
    } catch {
      if (active === generation.current && mounted.current) {
        setMessage('Lỗi tải. Dữ liệu đang hiển thị chưa được cập nhật.')
      }
    } finally {
      if (active === generation.current && mounted.current) setBusy(false)
    }
  }

  async function choose(item: Profile) {
    const active = ++generation.current
    credGen.current++
    setIssuedKey(null)
    setCreds([])
    setCredCursor(null)
    setCredLoaded(false)
    setCredUncertain(false)
    setCredMessage('')
    setBusy(true)
    try {
      const value = await requestJson<Profile>(`/api/v1/${kind}/${item.id}`)
      if (active !== generation.current || !mounted.current) return
      setSelected(value)
      setCode(value.device_id ?? value.gateway_id ?? '')
      setName(value.name)
      setNotes(value.notes ?? '')
      setUncertain(false)
      setMessage('')
    } catch {
      if (mounted.current) setMessage('Không tải được chi tiết. Hãy thử lại.')
    } finally {
      if (active === generation.current && mounted.current) setBusy(false)
    }
  }

  async function save() {
    if (operation.current || !canWrite()) return
    operation.current = true
    setBusy(true)
    const active = generation.current
    const body = selected
      ? { name, notes: notes || null, expected_row_version: selected.row_version }
      : {
          name,
          notes: notes || null,
          ...(kind === 'nodes'
            ? { device_id: code, zone_id: zone }
            : { gateway_id: code, farm_id: selectedFarmId }),
        }
    try {
      const value = await requestJson<Profile>(
        `/api/v1/${kind}${selected ? '/' + selected.id : ''}`,
        {
          method: selected ? 'PATCH' : 'POST',
          headers: mutationHeaders(csrf),
          body: JSON.stringify(body),
        },
      )
      if (!mounted.current || active !== generation.current) return
      setSelected(value)
      setName(value.name)
      setNotes(value.notes ?? '')
      setMessage('Đã lưu và được backend xác nhận.')
      setUncertain(false)
    } catch (error) {
      if (!mounted.current || active !== generation.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setMessage(
          error.status === 409
            ? 'Mã đã tồn tại, khu vực đã lưu trữ hoặc hồ sơ đã thay đổi. Đọc lại trước khi lưu tiếp.'
            : error.message,
        )
      } else {
        setUncertain(true)
        setMessage('Chưa xác nhận kết quả. Giữ biểu mẫu và kiểm tra lại hồ sơ trước khi gửi tiếp.')
      }
    } finally {
      operation.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function reconcile() {
    const active = generation.current
    setBusy(true)
    try {
      const key = kind === 'nodes' ? 'device_id' : 'gateway_id'
      const value = selected
        ? await requestJson<Profile>(`/api/v1/${kind}/${selected.id}`)
        : (await requestJson<Page>(`/api/v1/${kind}?${key}=${encodeURIComponent(code)}`)).items[0]
      if (!mounted.current || active !== generation.current) return
      setMessage(
        value
          ? `Hồ sơ đang lưu: tên “${value.name}”; ghi chú “${value.notes ?? ''}”. Biểu mẫu vẫn giữ nguyên. Chọn hồ sơ để nạp bản mới khi đã đối chiếu.`
          : 'Chưa tìm thấy mã trong lần đọc này. Biểu mẫu vẫn giữ nguyên.',
      )
      if (value) setItems(previous => [value, ...previous.filter(p => p.id !== value.id)])
    } catch {
      if (mounted.current) setMessage('Chưa đọc lại được. Hãy thử kiểm tra lại.')
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  async function loadCredentials(more = false) {
    if (!selected || kind !== 'gateways') return
    const active = ++credGen.current
    setBusy(true)
    try {
      const params = new URLSearchParams({ page_size: '10' })
      if (more && credCursor) params.set('cursor', credCursor)
      const page = await requestJson<CredentialPage>(
        `/api/v1/gateways/${selected.id}/credentials?${params}`,
      )
      if (!mounted.current || active !== credGen.current) return
      setCreds(prev => (more ? [...prev, ...page.items] : page.items))
      setCredCursor(page.next_cursor)
      setCredLoaded(true)
      setCredUncertain(false)
      setCredMessage('')
    } catch (error) {
      if (!mounted.current || active !== credGen.current) return
      setCredMessage(error instanceof ApiError ? error.message : 'Lỗi tải danh sách khóa.')
    } finally {
      if (mounted.current && active === credGen.current) setBusy(false)
    }
  }

  async function issueCredential() {
    if (!selected || kind !== 'gateways' || operation.current || !canWrite()) return
    operation.current = true
    setBusy(true)
    const active = credGen.current
    try {
      const res = await requestJson<CredentialIssueResponse>(
        `/api/v1/gateways/${selected.id}/credentials`,
        { method: 'POST', headers: mutationHeaders(csrf), body: JSON.stringify({}) },
      )
      if (!mounted.current || active !== credGen.current) return
      setIssuedKey(res.api_key)
      setCreds(prev => [res.credential, ...prev.filter(c => c.id !== res.credential.id)])
      setCredLoaded(true)
      setCredUncertain(false)
      setCredMessage('Đã cấp khóa mới. Lưu khóa ngay vì chỉ hiển thị một lần.')
    } catch (error) {
      if (!mounted.current || active !== credGen.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setCredMessage(
          error.code === 'CREDENTIAL_LIMIT_REACHED'
            ? 'Gateway đã có tối đa 2 khóa đang hoạt động. Hãy thu hồi một khóa cũ trước khi cấp mới.'
            : error.message,
        )
      } else {
        setCredUncertain(true)
        setCredMessage('Chưa xác nhận kết quả cấp khóa. Không tự cấp tiếp — hãy tải lại danh sách để đối chiếu.')
      }
    } finally {
      operation.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function revokeCredential(credId: string) {
    if (!selected || kind !== 'gateways' || operation.current || !canWrite()) return
    operation.current = true
    setBusy(true)
    const active = credGen.current
    try {
      const updated = await requestJson<CredentialView>(
        `/api/v1/gateways/${selected.id}/credentials/${credId}/revoke`,
        { method: 'POST', headers: mutationHeaders(csrf), body: JSON.stringify({}) },
      )
      if (!mounted.current || active !== credGen.current) return
      setCreds(prev => prev.map(c => (c.id === updated.id ? updated : c)))
      setCredMessage(`Đã thu hồi khóa #${updated.id}.`)
    } catch (error) {
      if (!mounted.current || active !== credGen.current) return
      setCredMessage(error instanceof ApiError ? error.message : 'Chưa xác nhận kết quả thu hồi khóa.')
    } finally {
      operation.current = false
      if (mounted.current) setBusy(false)
    }
  }

  return (
    <section aria-label="Hồ sơ thiết bị">
      <h2>Hồ sơ thiết bị và khóa Gateway</h2>
      <p>
        Danh mục metric:{' '}
        {metricsCatalog.length > 0
          ? metricsCatalog
              .map(m => `${m.code} (${m.unit}, ${m.min_value}..${m.max_value})`)
              .join(', ')
          : 'Đang tải…'}
      </p>
      <label>
        Loại hồ sơ{' '}
        <select
          disabled={busy}
          value={kind}
          onChange={e => setKind(e.target.value as typeof kind)}
        >
          <option value="nodes">Node</option>
          <option value="gateways">Gateway</option>
        </select>
      </label>
      <label>
        Mã chính xác{' '}
        <input
          disabled={busy}
          value={filter}
          onChange={e => {
            generation.current++
            setFilter(e.target.value)
            setItems([])
            setCursor(null)
            setLoaded(false)
          }}
        />
      </label>
      <button disabled={busy} onClick={() => void load()}>
        Tải danh sách
      </button>
      <button disabled={busy} onClick={() => void refreshAreasAndMetrics()}>
        Làm mới Farm
      </button>
      {loaded && items.length === 0 && <p>Chưa có hồ sơ khớp bộ lọc.</p>}
      <ul>
        {items.map(item => (
          <li key={item.id}>
            <button disabled={busy} onClick={() => void choose(item)}>
              {item.device_id ?? item.gateway_id} · {item.name}
            </button>
          </li>
        ))}
      </ul>
      {cursor && (
        <button disabled={busy} onClick={() => void load(true)}>
          Tải thêm
        </button>
      )}
      <button
        disabled={busy}
        onClick={() => {
          credGen.current++
          setSelected(null)
          setCode('')
          setName('')
          setNotes('')
          setUncertain(false)
          setMessage('')
          setIssuedKey(null)
          setCreds([])
          setCredLoaded(false)
        }}
      >
        Tạo hồ sơ mới
      </button>

      <form
        onSubmit={e => {
          e.preventDefault()
          void save()
        }}
      >
        <h3>{selected ? 'Sửa tên / ghi chú' : 'Đăng ký hồ sơ'}</h3>
        <label>
          Mã{' '}
          <input
            required
            disabled={busy || !!selected}
            value={code}
            onChange={e => setCode(e.target.value)}
          />
        </label>
        {!selected && (
          <label>
            Farm{' '}
            <select
              required
              disabled={busy}
              value={selectedFarmId}
              onChange={e => handleFarmChange(e.target.value)}
            >
              <option value="">Chọn Farm</option>
              {farms.map(f => (
                <option key={f.id} value={f.id}>
                  {f.code} · {f.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {!selected && kind === 'nodes' && (
          <label>
            Zone{' '}
            <select
              required
              disabled={busy || !selectedFarmId}
              value={zone}
              onChange={e => setZone(e.target.value)}
            >
              <option value="">Chọn Zone</option>
              {zones.map(z => (
                <option key={z.id} value={z.id}>
                  {z.code} · {z.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          Tên{' '}
          <input
            required
            disabled={busy}
            value={name}
            onChange={e => setName(e.target.value)}
          />
        </label>
        <label>
          Ghi chú{' '}
          <textarea
            disabled={busy}
            value={notes}
            onChange={e => setNotes(e.target.value)}
          />
        </label>
        <button
          disabled={
            busy ||
            !canWrite() ||
            uncertain ||
            (!selected && (kind === 'nodes' ? !zone : !selectedFarmId))
          }
        >
          Lưu
        </button>
      </form>

      {selected && (
        <div>
          <p>
            {selected.zone?.name ?? selected.farm?.name} · Quyền tiếp nhận:{' '}
            {selected.receiving_enabled ? 'Bật' : 'Tắt'}
          </p>
          {kind === 'nodes' && (
            <p>{selected.gateway?.code ?? 'Chưa liên kết Gateway'}</p>
          )}
          <p>
            Lần cuối lưu bản mới:{' '}
            {selected.last_new_persisted_at
              ? new Date(selected.last_new_persisted_at).toLocaleString('vi-VN', {
                  timeZone: 'Asia/Ho_Chi_Minh',
                })
              : 'Chưa có dữ liệu'}
          </p>
          <p>{selected.metrics?.map(m => `${m.metric_code} (${m.unit})`).join(', ')}</p>
          {selected.nodes && (
            <p>
              Node liên quan:{' '}
              {selected.nodes.map(n => n.name).join(', ') || 'Chưa có liên kết'}
            </p>
          )}

          {kind === 'gateways' && (
            <div aria-label="Quản lý khóa Gateway">
              <h4>Khóa API của Gateway</h4>
              <button disabled={busy} onClick={() => void loadCredentials()}>
                Tải danh sách khóa
              </button>
              <button
                disabled={busy || !canWrite() || credUncertain}
                onClick={() => void issueCredential()}
              >
                Cấp khóa mới
              </button>
              {issuedKey && (
                <div role="alert">
                  <p>
                    Khóa API mới (chỉ hiển thị một lần, hãy sao chép và lưu giữ an toàn):{' '}
                    <code>{issuedKey}</code>
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      void navigator.clipboard?.writeText(issuedKey)
                    }}
                  >
                    Sao chép khóa
                  </button>
                  <button type="button" onClick={() => setIssuedKey(null)}>
                    Đóng
                  </button>
                </div>
              )}
              {credLoaded && creds.length === 0 && <p>Gateway chưa có khóa nào.</p>}
              <ul>
                {creds.map(c => (
                  <li key={c.id}>
                    #{c.id} · {c.status} · Tạo:{' '}
                    {new Date(c.created_at).toLocaleString('vi-VN', {
                      timeZone: 'Asia/Ho_Chi_Minh',
                    })}
                    {c.revoked_at
                      ? ` · Thu hồi: ${new Date(c.revoked_at).toLocaleString('vi-VN', {
                          timeZone: 'Asia/Ho_Chi_Minh',
                        })}`
                      : ''}
                    {c.status === 'ACTIVE' && (
                      <button
                        disabled={busy || !canWrite()}
                        onClick={() => void revokeCredential(c.id)}
                      >
                        Thu hồi
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              {credCursor && (
                <button disabled={busy} onClick={() => void loadCredentials(true)}>
                  Tải thêm khóa
                </button>
              )}
              {credMessage && <p role="status">{credMessage}</p>}
            </div>
          )}
        </div>
      )}

      <p role="status">{busy ? 'Đang xử lý…' : message}</p>
      <button disabled={busy || !code} onClick={() => void reconcile()}>
        Kiểm tra lại hồ sơ, giữ biểu mẫu
      </button>
    </section>
  )
}
