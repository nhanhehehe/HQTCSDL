import { useEffect, useRef, useState } from 'react'
import { ApiError, requestJson } from '../../api/client'
import { useAuth } from '../auth/AuthProvider'

type Area = { farm: { id: string; name: string } | null; zones: { id: string; name: string }[]; configuration: { state: string; issues: { code: string }[] } }
type Profile = { id: string; device_id?: string; gateway_id?: string; name: string; notes: string | null; row_version: string; receiving_enabled: boolean; last_new_persisted_at: string | null; gateway?: { code: string } | null; zone?: { name: string }; farm?: { name: string }; nodes?: { id: string; name: string }[]; metrics?: { metric_code: string; unit: string }[] }
type Page = { items: Profile[]; next_cursor: string | null }

export function DevicesPanel() {
  const { session } = useAuth()
  return session ? <Devices key={session.csrf_token} csrf={session.csrf_token} /> : null
}

function Devices({ csrf }: { csrf: string }) {
  const [area, setArea] = useState<Area | null>(null)
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
  const generation = useRef(0)
  const operation = useRef(false)
  const mounted = useRef(true)

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current++ } }, [])
  useEffect(() => {
    const controller = new AbortController()
    requestJson<Area>('/api/v1/context', { signal: controller.signal }).then(value => {
      setArea(value); setZone(value.zones[0]?.id ?? '')
    }).catch(() => { if (!controller.signal.aborted) setMessage('Không tải được khu vực. Hãy tải lại trang.') })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    generation.current++
    setItems([]); setCursor(null); setSelected(null); setLoaded(false)
    setCode(''); setName(''); setNotes(''); setUncertain(false); setMessage('')
  }, [kind])

  async function load(more = false) {
    const active = ++generation.current
    setBusy(true)
    try {
      const params = new URLSearchParams()
      if (filter) params.set(kind === 'nodes' ? 'device_id' : 'gateway_id', filter)
      if (more && cursor) params.set('cursor', cursor)
      const value = await requestJson<Page>(`/api/v1/${kind}?${params}`)
      if (active !== generation.current || !mounted.current) return
      setItems(previous => more ? [...previous, ...value.items] : value.items)
      setCursor(value.next_cursor); setLoaded(true); setMessage('')
    } catch { if (active === generation.current && mounted.current) setMessage('Lỗi tải. Dữ liệu đang hiển thị chưa được cập nhật.') }
    finally { if (active === generation.current && mounted.current) setBusy(false) }
  }

  async function choose(item: Profile) {
    const active = ++generation.current
    setBusy(true)
    try {
      const value = await requestJson<Profile>(`/api/v1/${kind}/${item.id}`)
      if (active !== generation.current || !mounted.current) return
      setSelected(value); setCode(value.device_id ?? value.gateway_id ?? '')
      setName(value.name); setNotes(value.notes ?? ''); setUncertain(false); setMessage('')
    } catch { if (mounted.current) setMessage('Không tải được chi tiết. Hãy thử lại.') }
    finally { if (active === generation.current && mounted.current) setBusy(false) }
  }

  async function save() {
    if (operation.current) return
    operation.current = true; setBusy(true)
    const active = generation.current
    const body = selected ? { name, notes: notes || null, expected_row_version: selected.row_version }
      : { name, notes: notes || null, ...(kind === 'nodes' ? { device_id: code, zone_id: zone } : { gateway_id: code }) }
    try {
      const value = await requestJson<Profile>(`/api/v1/${kind}${selected ? '/' + selected.id : ''}`, {
        method: selected ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body),
      })
      if (!mounted.current || active !== generation.current) return
      setSelected(value); setName(value.name); setNotes(value.notes ?? '')
      setMessage('Đã lưu và được backend xác nhận.'); setUncertain(false)
    } catch (error) {
      if (!mounted.current || active !== generation.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setMessage(error.status === 409 ? 'Mã đã tồn tại hoặc hồ sơ đã thay đổi. Đọc lại và đối chiếu trước khi lưu tiếp.' : error.message)
      } else { setUncertain(true); setMessage('Chưa xác nhận kết quả. Giữ biểu mẫu và kiểm tra lại hồ sơ trước khi gửi tiếp.') }
    } finally { operation.current = false; if (mounted.current) setBusy(false) }
  }

  async function reconcile() {
    const active = generation.current
    setBusy(true)
    try {
      const key = kind === 'nodes' ? 'device_id' : 'gateway_id'
      const value = selected ? await requestJson<Profile>(`/api/v1/${kind}/${selected.id}`)
        : (await requestJson<Page>(`/api/v1/${kind}?${key}=${encodeURIComponent(code)}`)).items[0]
      if (!mounted.current || active !== generation.current) return
      setMessage(value ? `Hồ sơ đang lưu: tên “${value.name}”; ghi chú “${value.notes ?? ''}”. Biểu mẫu vẫn giữ nguyên. Chọn hồ sơ để nạp bản mới khi đã đối chiếu.` : 'Chưa tìm thấy mã trong lần đọc này. Biểu mẫu vẫn giữ nguyên.')
      if (value) setItems(previous => [value, ...previous.filter(p => p.id !== value.id)])
    } catch { if (mounted.current) setMessage('Chưa đọc lại được. Hãy thử kiểm tra lại.') }
    finally { if (mounted.current) setBusy(false) }
  }

  return <section aria-label="Hồ sơ thiết bị">
    <h2>Khu vực và thiết bị</h2>
    {area ? <p>{area.farm?.name ?? 'Thiếu Farm'} · {area.configuration.state} · {area.zones.map(z => z.name).join(', ')} {area.configuration.issues.map(i => i.code).join(', ')}</p> : <p>Chưa tải được khu vực</p>}
    <label>Loại hồ sơ <select disabled={busy} value={kind} onChange={e => setKind(e.target.value as typeof kind)}><option value="nodes">Node</option><option value="gateways">Gateway</option></select></label>
    <label>Mã chính xác <input disabled={busy} value={filter} onChange={e => { generation.current++; setFilter(e.target.value); setItems([]); setCursor(null); setLoaded(false) }} /></label>
    <button disabled={busy} onClick={() => void load()}>Tải danh sách</button>
    {loaded && items.length === 0 && <p>Chưa có hồ sơ khớp bộ lọc.</p>}
    <ul>{items.map(item => <li key={item.id}><button disabled={busy} onClick={() => void choose(item)}>{item.device_id ?? item.gateway_id} · {item.name}</button></li>)}</ul>
    {cursor && <button disabled={busy} onClick={() => void load(true)}>Tải thêm</button>}
    <button disabled={busy} onClick={() => { setSelected(null); setCode(''); setName(''); setNotes(''); setUncertain(false); setMessage('') }}>Tạo hồ sơ mới</button>
    <form onSubmit={e => { e.preventDefault(); void save() }}>
      <h3>{selected ? 'Sửa tên / ghi chú' : 'Đăng ký hồ sơ'}</h3>
      <label>Mã <input required disabled={busy || !!selected} value={code} onChange={e => setCode(e.target.value)} /></label>
      {!selected && kind === 'nodes' && <label>Zone <select required disabled={busy} value={zone} onChange={e => setZone(e.target.value)}><option value="">Chọn Zone</option>{area?.zones.map(z => <option key={z.id} value={z.id}>{z.name}</option>)}</select></label>}
      <label>Tên <input required disabled={busy} value={name} onChange={e => setName(e.target.value)} /></label>
      <label>Ghi chú <textarea disabled={busy} value={notes} onChange={e => setNotes(e.target.value)} /></label>
      <button disabled={busy || uncertain || (!selected && (kind === 'nodes' ? !zone : !area?.farm))}>Lưu</button>
    </form>
    {selected && <div><p>{selected.zone?.name ?? selected.farm?.name} · Quyền tiếp nhận: {selected.receiving_enabled ? 'Bật' : 'Tắt'}</p>{kind === 'nodes' && <p>{selected.gateway?.code ?? 'Chưa liên kết Gateway'}</p>}<p>Lần cuối lưu bản mới: {selected.last_new_persisted_at ? new Date(selected.last_new_persisted_at).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' }) : 'Chưa có dữ liệu'}</p><p>{selected.metrics?.map(m => `${m.metric_code} (${m.unit})`).join(', ')}</p>{selected.nodes && <p>Node liên quan: {selected.nodes.map(n => n.name).join(', ') || 'Chưa có liên kết'}</p>}</div>}
    <p role="status">{busy ? 'Đang xử lý…' : message}</p>
    <button disabled={busy || !code} onClick={() => void reconcile()}>Kiểm tra lại hồ sơ, giữ biểu mẫu</button>
  </section>
}
