import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, requestJson } from '../../api/client'
import { patchGatewayReceiving, patchNodeReceiving } from '../../api/monitoring'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { CursorPager } from '../../components/CursorPager'
import { DataTable } from '../../components/DataTable'
import type { Column } from '../../components/DataTable'
import { Drawer } from '../../components/Drawer'
import { EmptyState } from '../../components/EmptyState'
import { FilterBar } from '../../components/FilterBar'
import { Modal } from '../../components/Modal'
import { PageHeader } from '../../components/PageHeader'
import { StateBanner } from '../../components/StateBanner'
import type { BannerTone } from '../../components/StateBanner'
import { StatusBadge } from '../../components/StatusBadge'
import type { FarmView, ZoneView } from '../areas/types'
import { FarmZoneLoader } from '../areas/loader'
import { useAuth } from '../auth/AuthProvider'

type DeviceTab = 'nodes' | 'gateways' | 'metrics'

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

function formatViDateTime(iso: string | null): string {
  if (!iso) return 'Chưa có dữ liệu'
  return `${new Date(iso).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })} (UTC+07:00)`
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
  const profileFormId = useId()

  // Areas & read-only metrics catalog
  const [farms, setFarms] = useState<FarmView[]>([])
  const [selectedFarmId, setSelectedFarmId] = useState('')
  const [zones, setZones] = useState<ZoneView[]>([])
  const [metricsCatalog, setMetricsCatalog] = useState<MetricDef[]>([])
  const [metricsLoaded, setMetricsLoaded] = useState(false)

  // Tabs & list state
  const [activeTab, setActiveTab] = useState<DeviceTab>('nodes')
  const kind: 'nodes' | 'gateways' = activeTab === 'gateways' ? 'gateways' : 'nodes'
  const [items, setItems] = useState<Profile[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [selected, setSelected] = useState<Profile | null>(null)
  const [filter, setFilter] = useState('')
  const [loaded, setLoaded] = useState(false)

  // Drawer form state (separate from selected detail so cancelling Create preserves selection)
  const [drawerMode, setDrawerMode] = useState<'create' | 'edit' | null>(null)
  const [drawerTarget, setDrawerTarget] = useState<Profile | null>(null)
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [notes, setNotes] = useState('')
  const [zone, setZone] = useState('')
  const [drawerInitial, setDrawerInitial] = useState({
    code: '',
    name: '',
    notes: '',
    farmId: '',
    zone: '',
  })
  const [drawerConflict, setDrawerConflict] = useState(false)

  // Shared status & safety state
  const [message, setMessage] = useState('')
  const [messageTone, setMessageTone] = useState<BannerTone>('info')
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)

  // Gateway credentials state
  const [creds, setCreds] = useState<CredentialView[]>([])
  const [credCursor, setCredCursor] = useState<string | null>(null)
  const [credLoaded, setCredLoaded] = useState(false)
  const [issuedKey, setIssuedKey] = useState<string | null>(null)
  const [copiedKey, setCopiedKey] = useState(false)
  const [credUncertain, setCredUncertain] = useState(false)
  const [credMessage, setCredMessage] = useState('')
  const [credMessageTone, setCredMessageTone] = useState<BannerTone>('info')
  const [revokeTarget, setRevokeTarget] = useState<CredentialView | null>(null)
  const [receivingConfirmTarget, setReceivingConfirmTarget] = useState<{
    profile: Profile
    nextEnabled: boolean
  } | null>(null)

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

  const refreshAreasAndMetrics = useCallback(async (signal?: AbortSignal) => {
    try {
      const [allFarms, metricsRes] = await Promise.all([
        areaLoader.current.loadActiveFarms(signal),
        requestJson<{ items: MetricDef[] }>('/api/v1/metrics', { signal }),
      ])
      if (!mounted.current || signal?.aborted) return
      setFarms(allFarms)
      setMetricsCatalog(metricsRes.items)
      setMetricsLoaded(true)
    } catch {
      if (!signal?.aborted && mounted.current) {
        setMessageTone('err')
        setMessage('Không tải được danh sách Farm hoặc danh mục metric.')
      }
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void refreshAreasAndMetrics(controller.signal)
    return () => controller.abort()
  }, [refreshAreasAndMetrics])

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
        if (mounted.current) {
          setMessageTone('err')
          setMessage('Không tải được Zone thuộc Farm đã chọn.')
        }
      },
    })
  }

  const loadList = useCallback(
    async (
      targetKind: 'nodes' | 'gateways',
      targetFilter: string,
      pageCursor: string | null,
      more: boolean,
    ) => {
      const active = ++generation.current
      setBusy(true)
      try {
        const params = new URLSearchParams({ page_size: '10' })
        if (targetFilter.trim()) {
          params.set(targetKind === 'nodes' ? 'device_id' : 'gateway_id', targetFilter.trim())
        }
        if (more && pageCursor) params.set('cursor', pageCursor)
        const value = await requestJson<Page>(`/api/v1/${targetKind}?${params}`)
        if (active !== generation.current || !mounted.current) return
        setItems(previous => (more ? [...previous, ...value.items] : value.items))
        setCursor(value.next_cursor)
        setLoaded(true)
        if (!more) {
          setMessage('')
        }
      } catch {
        if (active === generation.current && mounted.current) {
          setMessageTone('err')
          setMessage('Lỗi tải. Dữ liệu đang hiển thị chưa được cập nhật.')
        }
      } finally {
        if (active === generation.current && mounted.current) setBusy(false)
      }
    },
    [],
  )

  const loadCredentialsForGateway = useCallback(
    async (gatewayId: string, pageCursor: string | null, more: boolean) => {
      const active = ++credGen.current
      setBusy(true)
      try {
        const params = new URLSearchParams({ page_size: '10' })
        if (more && pageCursor) params.set('cursor', pageCursor)
        const page = await requestJson<CredentialPage>(
          `/api/v1/gateways/${gatewayId}/credentials?${params}`,
        )
        if (!mounted.current || active !== credGen.current) return
        setCreds(prev => (more ? [...prev, ...page.items] : page.items))
        setCredCursor(page.next_cursor)
        setCredLoaded(true)
        setCredUncertain(false)
        if (!more) {
          setCredMessage('')
        }
      } catch (error) {
        if (!mounted.current || active !== credGen.current) return
        setCredMessageTone('err')
        setCredMessage(error instanceof ApiError ? error.message : 'Lỗi tải danh sách khóa.')
      } finally {
        if (mounted.current && active === credGen.current) setBusy(false)
      }
    },
    [],
  )

  useEffect(() => {
    if (activeTab === 'metrics') {
      setIssuedKey(null)
      setCopiedKey(false)
      setDrawerMode(null)
      setDrawerTarget(null)
      setRevokeTarget(null)
      return
    }
    generation.current++
    credGen.current++
    setItems([])
    setCursor(null)
    setSelected(null)
    setLoaded(false)
    setFilter('')
    setDrawerMode(null)
    setDrawerTarget(null)
    setDrawerConflict(false)
    setCode('')
    setName('')
    setNotes('')
    setUncertain(false)
    setMessage('')
    setCreds([])
    setCredCursor(null)
    setCredLoaded(false)
    setIssuedKey(null)
    setCopiedKey(false)
    setCredUncertain(false)
    setCredMessage('')
    setRevokeTarget(null)
    setReceivingConfirmTarget(null)
    void loadList(activeTab, '', null, false)
  }, [activeTab, loadList])

  async function choose(item: Profile) {
    const active = ++generation.current
    credGen.current++
    setIssuedKey(null)
    setCopiedKey(false)
    setCreds([])
    setCredCursor(null)
    setCredLoaded(false)
    setCredUncertain(false)
    setCredMessage('')
    setRevokeTarget(null)
    setReceivingConfirmTarget(null)
    setBusy(true)
    try {
      const value = await requestJson<Profile>(`/api/v1/${kind}/${item.id}`)
      if (active !== generation.current || !mounted.current) return
      setSelected(value)
      setUncertain(false)
      setMessage('')
      if (kind === 'gateways') {
        void loadCredentialsForGateway(value.id, null, false)
      }
    } catch {
      if (mounted.current) {
        setMessageTone('err')
        setMessage('Không tải được chi tiết. Hãy thử lại.')
      }
    } finally {
      if (active === generation.current && mounted.current) setBusy(false)
    }
  }

  function openCreateDrawer() {
    credGen.current++
    setIssuedKey(null)
    setCopiedKey(false)
    areaLoader.current.dispose()
    setDrawerMode('create')
    setDrawerTarget(null)
    setCode('')
    setName('')
    setNotes('')
    setSelectedFarmId('')
    setZones([])
    setZone('')
    setDrawerInitial({ code: '', name: '', notes: '', farmId: '', zone: '' })
    setDrawerConflict(false)
    setUncertain(false)
    setMessage('')
  }

  function openEditDrawer(profile: Profile) {
    setIssuedKey(null)
    setCopiedKey(false)
    const pCode = profile.device_id ?? profile.gateway_id ?? ''
    const pNotes = profile.notes ?? ''
    setSelected(profile)
    setDrawerMode('edit')
    setDrawerTarget(profile)
    setCode(pCode)
    setName(profile.name)
    setNotes(pNotes)
    setSelectedFarmId('')
    setZone('')
    setDrawerInitial({ code: pCode, name: profile.name, notes: pNotes, farmId: '', zone: '' })
    setDrawerConflict(false)
    setUncertain(false)
    setMessage('')
  }

  async function save() {
    if (operation.current || !canWrite()) return
    const isPatch = drawerMode === 'edit' && drawerTarget !== null
    operation.current = true
    setBusy(true)
    setDrawerConflict(false)
    const active = generation.current
    const body = isPatch
      ? { name, notes: notes || null, expected_row_version: drawerTarget.row_version }
      : {
          name,
          notes: notes || null,
          ...(kind === 'nodes'
            ? { device_id: code, zone_id: zone }
            : { gateway_id: code, farm_id: selectedFarmId }),
        }
    try {
      const value = await requestJson<Profile>(
        `/api/v1/${kind}${isPatch ? '/' + drawerTarget.id : ''}`,
        {
          method: isPatch ? 'PATCH' : 'POST',
          headers: mutationHeaders(csrf),
          body: JSON.stringify(body),
        },
      )
      if (!mounted.current || active !== generation.current) return
      setSelected(value)
      setItems(prev => [value, ...prev.filter(p => p.id !== value.id)])
      setDrawerMode(null)
      setDrawerTarget(null)
      setDrawerConflict(false)
      setUncertain(false)
      setMessageTone('ok')
      setMessage('Đã lưu và được backend xác nhận.')
      if (!isPatch && kind === 'gateways') {
        void loadCredentialsForGateway(value.id, null, false)
      }
    } catch (error) {
      if (!mounted.current || active !== generation.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setMessageTone('err')
        setMessage(
          error.status === 409
            ? 'Mã đã tồn tại, khu vực đã lưu trữ hoặc hồ sơ đã thay đổi. Đọc lại trước khi lưu tiếp.'
            : error.message,
        )
        if (error.status === 409) {
          setDrawerConflict(true)
        }
      } else {
        setUncertain(true)
        setMessageTone('warn')
        setMessage(
          'Chưa xác nhận kết quả. Giữ biểu mẫu và kiểm tra lại hồ sơ trước khi gửi tiếp.',
        )
      }
    } finally {
      operation.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function reconcile(reloadLatestIntoForm = false) {
    const active = generation.current
    setBusy(true)
    try {
      const key = kind === 'nodes' ? 'device_id' : 'gateway_id'
      const targetId = drawerTarget?.id ?? selected?.id
      const value = targetId
        ? await requestJson<Profile>(`/api/v1/${kind}/${targetId}`)
        : (
            await requestJson<Page>(
              `/api/v1/${kind}?${key}=${encodeURIComponent(code)}`,
            )
          ).items[0]
      if (!mounted.current || active !== generation.current) return
      if (value) {
        setItems(previous => [value, ...previous.filter(p => p.id !== value.id)])
        if (selected?.id === value.id) {
          setSelected(value)
        }
        if (reloadLatestIntoForm && drawerMode === 'edit') {
          const pCode = value.device_id ?? value.gateway_id ?? ''
          const pNotes = value.notes ?? ''
          setDrawerTarget(value)
          setCode(pCode)
          setName(value.name)
          setNotes(pNotes)
          setDrawerInitial({ code: pCode, name: value.name, notes: pNotes, farmId: '', zone: '' })
          setDrawerConflict(false)
          setUncertain(false)
          setMessageTone('info')
          setMessage(`Đã nạp bản mới nhất của hồ sơ ${pCode} vào biểu mẫu.`)
        } else {
          setMessageTone('info')
          setMessage(
            `Hồ sơ đang lưu: tên “${value.name}”; ghi chú “${value.notes ?? ''}”. Biểu mẫu vẫn giữ nguyên. Chọn hồ sơ để nạp bản mới khi đã đối chiếu.`,
          )
        }
      } else {
        setMessageTone('info')
        setMessage('Chưa tìm thấy mã trong lần đọc này. Biểu mẫu vẫn giữ nguyên.')
      }
    } catch {
      if (mounted.current) {
        setMessageTone('err')
        setMessage('Chưa đọc lại được. Hãy thử kiểm tra lại.')
      }
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  async function toggleReceiving(target: Profile, nextEnabled: boolean) {
    if (operation.current || !canWrite()) return
    operation.current = true
    setBusy(true)
    const active = generation.current
    try {
      if (kind === 'nodes') {
        const res = await patchNodeReceiving(
          target.id,
          {
            receiving_enabled: nextEnabled,
            expected_row_version: target.row_version,
          },
          csrf,
        )
        if (!mounted.current || active !== generation.current) return
        const updated: Profile = {
          ...target,
          receiving_enabled: res.receiving_enabled,
          row_version: res.profile_row_version,
        }
        setSelected(prev => (prev?.id === updated.id ? updated : prev))
        setItems(prev => prev.map(item => (item.id === updated.id ? updated : item)))
        setUncertain(false)
        setMessageTone('ok')
        setMessage(
          res.receiving_enabled
            ? `Đã bật quyền tiếp nhận dữ liệu mới cho Node ${target.device_id ?? target.name}.`
            : `Đã tắt quyền tiếp nhận dữ liệu mới cho Node ${target.device_id ?? target.name} (đóng mọi period/episode OPEN theo FL-04).`,
        )
      } else {
        const res = await patchGatewayReceiving(
          target.id,
          {
            receiving_enabled: nextEnabled,
            expected_row_version: target.row_version,
          },
          csrf,
        )
        if (!mounted.current || active !== generation.current) return
        const updated: Profile = {
          ...target,
          receiving_enabled: res.receiving_enabled,
          row_version: res.profile_row_version,
        }
        setSelected(prev => (prev?.id === updated.id ? updated : prev))
        setItems(prev => prev.map(item => (item.id === updated.id ? updated : item)))
        setReceivingConfirmTarget(null)
        setUncertain(false)
        setMessageTone('ok')
        setMessage(
          res.receiving_enabled
            ? `Đã bật quyền tiếp nhận cho Gateway ${target.gateway_id ?? target.name} (${res.affected_nodes.length} Node liên kết).`
            : `Đã tắt quyền tiếp nhận cho Gateway ${target.gateway_id ?? target.name} (${res.affected_nodes.length} Node liên kết đều bị tắt đồng bộ).`,
        )
      }
    } catch (error) {
      if (!mounted.current || active !== generation.current) return
      setReceivingConfirmTarget(null)
      if (error instanceof ApiError && error.status !== 503) {
        setMessageTone('err')
        if (error.status === 409) {
          setMessage(
            error.code === 'AREA_ARCHIVED'
              ? 'Farm hoặc Zone đã lưu trữ nên không thể đổi quyền tiếp nhận.'
              : 'Hồ sơ thiết bị đã thay đổi phiên bản (409). Hãy chọn lại hoặc tải lại hồ sơ trước khi thao tác.',
          )
        } else {
          setMessage(error.message)
        }
      } else {
        setUncertain(true)
        setMessageTone('warn')
        setMessage(
          'Chưa xác nhận kết quả đổi quyền tiếp nhận. Nhấn “Đọc lại hồ sơ” để kiểm tra trạng thái thực tế.',
        )
      }
    } finally {
      operation.current = false
      if (mounted.current) setBusy(false)
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
      setCopiedKey(false)
      setCreds(prev => [res.credential, ...prev.filter(c => c.id !== res.credential.id)])
      setCredLoaded(true)
      setCredUncertain(false)
      setCredMessageTone('ok')
      setCredMessage('Đã cấp khóa mới. Lưu khóa ngay vì chỉ hiển thị một lần.')
    } catch (error) {
      if (!mounted.current || active !== credGen.current) return
      if (error instanceof ApiError && error.status !== 503) {
        setCredMessageTone('err')
        setCredMessage(
          error.code === 'CREDENTIAL_LIMIT_REACHED'
            ? 'Gateway đã có tối đa 2 khóa đang hoạt động. Hãy thu hồi một khóa cũ trước khi cấp mới.'
            : error.message,
        )
      } else {
        setCredUncertain(true)
        setCredMessageTone('warn')
        setCredMessage(
          'Chưa xác nhận kết quả cấp khóa. Không tự cấp tiếp — hãy tải lại danh sách để đối chiếu.',
        )
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
      setRevokeTarget(null)
      setCredMessageTone('ok')
      setCredMessage(`Đã thu hồi khóa #${updated.id}.`)
    } catch (error) {
      if (!mounted.current || active !== credGen.current) return
      setRevokeTarget(null)
      if (error instanceof ApiError && error.status !== 503) {
        setCredMessageTone('err')
        setCredMessage(error.message)
      } else {
        setCredUncertain(true)
        setCredMessageTone('warn')
        setCredMessage('Chưa xác nhận kết quả thu hồi khóa. Hãy tải lại danh sách khóa.')
      }
    } finally {
      operation.current = false
      if (mounted.current) setBusy(false)
    }
  }

  const drawerDirty =
    drawerMode !== null &&
    (code !== drawerInitial.code ||
      name !== drawerInitial.name ||
      notes !== drawerInitial.notes ||
      selectedFarmId !== drawerInitial.farmId ||
      zone !== drawerInitial.zone)

  const profileColumns: Column<Profile>[] = [
    {
      key: 'code',
      header: kind === 'nodes' ? 'Mã Node' : 'Mã Gateway',
      render: item => <span className="mono">{item.device_id ?? item.gateway_id}</span>,
    },
    {
      key: 'name',
      header: 'Tên hồ sơ',
      render: item => <strong>{item.name}</strong>,
    },
    {
      key: 'area',
      header: kind === 'nodes' ? 'Farm / Zone' : 'Farm',
      render: item =>
        kind === 'nodes'
          ? `${item.farm?.code ?? '—'} / ${item.zone?.code ?? '—'}`
          : (item.farm?.code ?? '—'),
    },
    {
      key: 'receiving',
      header: 'Tiếp nhận',
      render: item => (
        <StatusBadge
          tone={item.receiving_enabled ? 'ok' : 'neutral'}
          label={item.receiving_enabled ? 'Đang bật' : 'Đã tắt'}
        />
      ),
    },
    {
      key: 'select',
      header: 'Chi tiết',
      render: item => (
        <button
          type="button"
          className={selected?.id === item.id ? 'btn-sm' : 'btn-secondary btn-sm'}
          disabled={busy}
          onClick={() => void choose(item)}
        >
          {selected?.id === item.id ? 'Đang xem' : 'Xem'}
        </button>
      ),
    },
  ]

  const credentialColumns: Column<CredentialView>[] = [
    {
      key: 'id',
      header: 'ID',
      render: c => <span className="mono">#{c.id}</span>,
    },
    {
      key: 'status',
      header: 'Trạng thái',
      render: c => (
        <StatusBadge
          tone={c.status === 'ACTIVE' ? 'ok' : 'err'}
          label={c.status}
        />
      ),
    },
    {
      key: 'created_at',
      header: 'Tạo lúc (+07:00)',
      className: 'tabular',
      render: c => new Date(c.created_at).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' }),
    },
    {
      key: 'revoked_at',
      header: 'Thu hồi lúc',
      className: 'tabular',
      render: c =>
        c.revoked_at
          ? new Date(c.revoked_at).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })
          : '—',
    },
    {
      key: 'action',
      header: 'Thao tác',
      render: c =>
        c.status === 'ACTIVE' ? (
          <button
            type="button"
            className="btn-danger btn-sm"
            disabled={busy || !canWrite()}
            onClick={() => setRevokeTarget(c)}
          >
            Thu hồi
          </button>
        ) : (
          <span className="muted">—</span>
        ),
    },
  ]

  const metricsColumns: Column<MetricDef>[] = [
    {
      key: 'code',
      header: 'Mã đại lượng',
      render: m => <span className="mono">{m.code}</span>,
    },
    {
      key: 'unit',
      header: 'Đơn vị',
      render: m => <strong>{m.unit}</strong>,
    },
    {
      key: 'range',
      header: 'Miền kỹ thuật hợp lệ',
      className: 'tabular mono',
      render: m => `${m.min_value} .. ${m.max_value}`,
    },
    {
      key: 'decimals',
      header: 'Chữ số thập phân',
      className: 'tabular',
      render: m => m.decimal_places,
    },
  ]

  const activeCredCount = creds.filter(c => c.status === 'ACTIVE').length

  return (
    <div className="stack" aria-label="Hồ sơ thiết bị">
      <PageHeader
        eyebrow="Quản trị hệ thống · FL-03 (G1)"
        title="Hồ sơ thiết bị & Khóa Gateway"
        description="Đăng ký, tra cứu và cập nhật hồ sơ Node/Gateway theo Farm/Zone trong SQL Server; cấp và thu hồi API Key Gateway."
        actions={
          <div className="row">
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => void refreshAreasAndMetrics()}
            >
              Làm mới Farm & Metric
            </button>
            {activeTab !== 'metrics' && (
              <button type="button" disabled={busy || !canWrite()} onClick={openCreateDrawer}>
                {kind === 'nodes' ? '+ Đăng ký Node mới' : '+ Đăng ký Gateway mới'}
              </button>
            )}
          </div>
        }
      />

      {/* Tabs chuyển giữa Nodes / Gateways / Danh mục metric */}
      <div className="tabs" role="tablist" aria-label="Phân nhóm thiết bị">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'nodes'}
          className={`tab-btn${activeTab === 'nodes' ? ' active' : ''}`}
          disabled={busy}
          onClick={() => setActiveTab('nodes')}
        >
          Nodes
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'gateways'}
          className={`tab-btn${activeTab === 'gateways' ? ' active' : ''}`}
          disabled={busy}
          onClick={() => setActiveTab('gateways')}
        >
          Gateways & API Keys
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'metrics'}
          className={`tab-btn${activeTab === 'metrics' ? ' active' : ''}`}
          disabled={busy}
          onClick={() => setActiveTab('metrics')}
        >
          Danh mục đại lượng ({metricsCatalog.length || 5})
        </button>
      </div>

      {(busy || message) && (
        <StateBanner
          tone={busy ? 'info' : messageTone}
          message={busy ? 'Đang xử lý yêu cầu với Backend…' : message}
          action={
            uncertain && (code || selected) && activeTab !== 'metrics' ? (
              <button
                type="button"
                className="btn-secondary btn-sm"
                disabled={busy}
                onClick={() => void reconcile(false)}
              >
                Kiểm tra lại hồ sơ
              </button>
            ) : undefined
          }
        />
      )}

      {activeTab === 'metrics' ? (
        <section className="card stack" aria-label="Danh mục đại lượng chỉ đọc">
          <div className="card-header">
            <div className="stack-sm">
              <h2>Danh mục 5 đại lượng chuẩn (Chỉ đọc)</h2>
              <small>Dữ liệu đọc trực tiếp từ GET /api/v1/metrics (bảng MetricDefinition).</small>
            </div>
          </div>
          <DataTable
            columns={metricsColumns}
            items={metricsCatalog}
            getKey={m => m.code}
            loading={!metricsLoaded}
            loaded={metricsLoaded}
            emptyTitle="Chưa tải được danh mục đại lượng"
          />
        </section>
      ) : (
        <div className="master-detail-balanced">
          {/* Cột trái: Bộ lọc & Danh sách Nodes / Gateways */}
          <section className="card stack" aria-label={kind === 'nodes' ? 'Danh sách Node' : 'Danh sách Gateway'}>
            <div className="card-header">
              <h2>{kind === 'nodes' ? 'Danh sách Nodes' : 'Danh sách Gateways'}</h2>
            </div>

            <form
              onSubmit={event => {
                event.preventDefault()
                void loadList(kind, filter, null, false)
              }}
            >
              <FilterBar>
                <label className="filter-field">
                  {kind === 'nodes' ? 'Mã Node chính xác (device_id)' : 'Mã Gateway chính xác (gateway_id)'}
                  <input
                    disabled={busy}
                    placeholder={kind === 'nodes' ? 'Ví dụ: NODE-01' : 'Ví dụ: GW-01'}
                    value={filter}
                    onChange={event => {
                      generation.current++
                      setFilter(event.target.value)
                      setItems([])
                      setCursor(null)
                      setLoaded(false)
                    }}
                  />
                </label>

                <div className="filter-actions">
                  <button type="submit" disabled={busy}>
                    Tải danh sách
                  </button>
                  {filter && (
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={busy}
                      onClick={() => {
                        setFilter('')
                        void loadList(kind, '', null, false)
                      }}
                    >
                      Đặt lại
                    </button>
                  )}
                </div>
              </FilterBar>
            </form>

            <DataTable
              columns={profileColumns}
              items={items}
              getKey={p => p.id}
              selectedKey={selected?.id ?? null}
              loading={busy}
              loaded={loaded}
              emptyTitle="Chưa có hồ sơ khớp bộ lọc"
              emptyDescription="Nhấn nút Đăng ký mới ở góc trên hoặc thay đổi mã tìm kiếm."
            />

            {loaded && (
              <CursorPager
                count={items.length}
                nextCursor={cursor}
                busy={busy}
                onLoadMore={() => void loadList(kind, filter, cursor, true)}
              />
            )}
          </section>

          {/* Cột phải: Chi tiết hồ sơ & Credentials (nếu là Gateway) */}
          <section className="card stack" aria-label="Chi tiết thiết bị">
            {!selected ? (
              <EmptyState
                title={kind === 'nodes' ? 'Chưa chọn Node' : 'Chưa chọn Gateway'}
                description="Chọn một hồ sơ ở danh sách bên trái để xem chi tiết cấu hình hoặc quản lý khóa API."
              />
            ) : (
              <>
                <div className="card-header">
                  <div className="stack-sm">
                    <div className="row">
                      <h2>
                        <span className="mono">{selected.device_id ?? selected.gateway_id}</span> ·{' '}
                        {selected.name}
                      </h2>
                      <StatusBadge
                        tone={selected.receiving_enabled ? 'ok' : 'neutral'}
                        label={
                          selected.receiving_enabled
                            ? 'Đang nhận dữ liệu mới'
                            : 'Đã tắt tiếp nhận mới'
                        }
                      />
                    </div>
                    <small>
                      {kind === 'nodes'
                        ? 'Bật/tắt quyền tiếp nhận Node (FL-04): khi tắt sẽ khóa nhận mẫu mới và đóng period/episode OPEN.'
                        : 'Bật/tắt quyền tiếp nhận Gateway (FL-04): thay đổi áp dụng đồng bộ xuống toàn bộ Node thuộc Gateway này.'}
                    </small>
                  </div>

                  <div className="row">
                    {kind === 'nodes' && (
                      <Link className="btn btn-secondary btn-sm" to={`/monitoring?node_id=${encodeURIComponent(selected.id)}`}>
                        Cấu hình giám sát
                      </Link>
                    )}
                    <button
                      type="button"
                      className={selected.receiving_enabled ? 'btn-danger btn-sm' : 'btn-secondary btn-sm'}
                      disabled={busy || !canWrite()}
                      onClick={() => {
                        const nextEnabled = !selected.receiving_enabled
                        if (kind === 'gateways') {
                          setReceivingConfirmTarget({ profile: selected, nextEnabled })
                        } else {
                          void toggleReceiving(selected, nextEnabled)
                        }
                      }}
                    >
                      {selected.receiving_enabled ? 'Tắt tiếp nhận' : 'Bật tiếp nhận'}
                    </button>
                    <button
                      type="button"
                      className="btn-secondary btn-sm"
                      disabled={busy}
                      onClick={() => openEditDrawer(selected)}
                    >
                      Chỉnh sửa
                    </button>
                  </div>
                </div>

                <dl className="kv-grid">
                  <div className="kv-item">
                    <dt>ID nội bộ</dt>
                    <dd className="mono">{selected.id}</dd>
                  </div>
                  <div className="kv-item">
                    <dt>Farm</dt>
                    <dd>
                      {selected.farm ? `${selected.farm.code} · ${selected.farm.name}` : '—'}
                    </dd>
                  </div>
                  {kind === 'nodes' && (
                    <>
                      <div className="kv-item">
                        <dt>Zone (không đổi qua sửa hồ sơ)</dt>
                        <dd>
                          {selected.zone ? `${selected.zone.code} · ${selected.zone.name}` : '—'}
                        </dd>
                      </div>
                      <div className="kv-item">
                        <dt>Gateway liên kết (chỉ đọc)</dt>
                        <dd className="mono">{selected.gateway?.code ?? 'Chưa liên kết Gateway'}</dd>
                      </div>
                    </>
                  )}
                  <div className="kv-item">
                    <dt>Lần cuối lưu bản mới</dt>
                    <dd className="tabular">{formatViDateTime(selected.last_new_persisted_at)}</dd>
                  </div>
                  <div className="kv-item">
                    <dt>Ghi chú</dt>
                    <dd>{selected.notes || 'Không có ghi chú'}</dd>
                  </div>
                </dl>

                {selected.metrics && selected.metrics.length > 0 && (
                  <div className="stack-sm">
                    <small>
                      <strong>Đại lượng gắn kèm:</strong>{' '}
                      {selected.metrics.map(m => `${m.metric_code} (${m.unit})`).join(', ')}
                    </small>
                  </div>
                )}

                {selected.nodes && (
                  <div className="stack-sm">
                    <small>
                      <strong>Node liên quan (chỉ đọc):</strong>{' '}
                      {selected.nodes.map(n => `${n.device_id} · ${n.name}`).join(', ') ||
                        'Chưa có liên kết'}
                    </small>
                  </div>
                )}

                {kind === 'gateways' && (
                  <>
                    <hr
                      style={{
                        border: 0,
                        borderTop: '1px solid var(--color-border)',
                        margin: 0,
                      }}
                    />
                    <div className="stack" aria-label="Quản lý khóa Gateway">
                      <div className="card-header">
                        <div className="stack-sm">
                          <h3>Khóa API của Gateway</h3>
                          <small className="tabular">
                            Đang hoạt động (trên các trang đã tải): {activeCredCount} / tối đa 2
                            khóa ACTIVE
                          </small>
                        </div>

                        <div className="row">
                          <button
                            type="button"
                            className="btn-secondary btn-sm"
                            disabled={busy}
                            onClick={() =>
                              void loadCredentialsForGateway(selected.id, null, false)
                            }
                          >
                            Tải lại danh sách khóa
                          </button>
                          <button
                            type="button"
                            className="btn-sm"
                            disabled={busy || !canWrite() || credUncertain}
                            onClick={() => void issueCredential()}
                          >
                            + Cấp khóa mới
                          </button>
                        </div>
                      </div>

                      {credMessage && (
                        <StateBanner
                          tone={credUncertain ? 'warn' : credMessageTone}
                          message={credMessage}
                          action={
                            credUncertain ? (
                              <button
                                type="button"
                                className="btn-secondary btn-sm"
                                disabled={busy}
                                onClick={() =>
                                  void loadCredentialsForGateway(selected.id, null, false)
                                }
                              >
                                Tải lại danh sách khóa
                              </button>
                            ) : undefined
                          }
                        />
                      )}

                      <DataTable
                        columns={credentialColumns}
                        items={creds}
                        getKey={c => c.id}
                        loading={busy && !credLoaded}
                        loaded={credLoaded}
                        emptyTitle="Gateway chưa có khóa nào"
                        emptyDescription="Nhấn Cấp khóa mới để tạo khóa xác thực X-API-Key cho Gateway."
                      />

                      {credLoaded && (
                        <CursorPager
                          count={creds.length}
                          nextCursor={credCursor}
                          busy={busy}
                          onLoadMore={() =>
                            void loadCredentialsForGateway(selected.id, credCursor, true)
                          }
                        />
                      )}
                    </div>
                  </>
                )}
              </>
            )}
          </section>
        </div>
      )}

      {/* Drawer Đăng ký / Sửa Node hoặc Gateway */}
      <Drawer
        open={drawerMode !== null}
        title={
          drawerMode === 'edit' && drawerTarget
            ? `Sửa ${kind === 'nodes' ? 'Node' : 'Gateway'}: ${drawerTarget.device_id ?? drawerTarget.gateway_id}`
            : `Đăng ký ${kind === 'nodes' ? 'Node' : 'Gateway'} mới`
        }
        onClose={() => {
          areaLoader.current.dispose()
          setDrawerMode(null)
          setDrawerTarget(null)
          setDrawerConflict(false)
        }}
        busy={busy}
        dirty={drawerDirty}
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
              form={profileFormId}
              disabled={
                busy ||
                !canWrite() ||
                uncertain ||
                (drawerMode === 'create' && (kind === 'nodes' ? !zone : !selectedFarmId))
              }
            >
              {busy ? 'Đang lưu…' : 'Lưu hồ sơ'}
            </button>
          </>
        )}
      >
        {(message || uncertain || drawerConflict) && (
          <StateBanner
            tone={uncertain ? 'warn' : messageTone}
            message={
              message ||
              'Chưa xác nhận kết quả. Giữ biểu mẫu và kiểm tra lại hồ sơ trước khi gửi tiếp.'
            }
            action={
              (drawerConflict || uncertain) && (code || drawerTarget) ? (
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  disabled={busy}
                  onClick={() => void reconcile(drawerConflict && drawerMode === 'edit')}
                >
                  {drawerConflict && drawerMode === 'edit'
                    ? 'Tải lại bản mới nhất'
                    : 'Kiểm tra lại hồ sơ, giữ biểu mẫu'}
                </button>
              ) : undefined
            }
          />
        )}

        <form
          id={profileFormId}
          className="stack"
          onSubmit={event => {
            event.preventDefault()
            void save()
          }}
        >
          <label>
            {kind === 'nodes' ? 'Mã Node (device_id)' : 'Mã Gateway (gateway_id)'}
            <input
              required
              disabled={busy || drawerMode === 'edit'}
              value={code}
              onChange={event => setCode(event.target.value)}
            />
          </label>

          {drawerMode === 'create' && (
            <label>
              Farm (Đang hoạt động)
              <select
                required
                disabled={busy}
                value={selectedFarmId}
                onChange={event => handleFarmChange(event.target.value)}
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

          {drawerMode === 'create' && kind === 'nodes' && (
            <label>
              Zone (Đang hoạt động)
              <select
                required
                disabled={busy || !selectedFarmId}
                value={zone}
                onChange={event => setZone(event.target.value)}
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
            Tên hiển thị
            <input
              required
              disabled={busy}
              value={name}
              onChange={event => setName(event.target.value)}
            />
          </label>

          <label>
            Ghi chú
            <textarea
              disabled={busy}
              value={notes}
              onChange={event => setNotes(event.target.value)}
            />
          </label>
        </form>
      </Drawer>

      {/* Modal Show-Once API Key */}
      <Modal
        open={issuedKey !== null}
        title="Khóa API Gateway mới (Chỉ hiển thị một lần)"
        disableBackdropClose
        onClose={() => {
          setIssuedKey(null)
          setCopiedKey(false)
        }}
        footer={
          <>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                if (!issuedKey) return
                void navigator.clipboard
                  ?.writeText(issuedKey)
                  .then(() => {
                    if (mounted.current) setCopiedKey(true)
                  })
                  .catch(() => {
                    if (mounted.current) setCopiedKey(false)
                  })
              }}
            >
              {copiedKey ? 'Đã sao chép khóa' : 'Sao chép khóa'}
            </button>
            <button
              type="button"
              onClick={() => {
                setIssuedKey(null)
                setCopiedKey(false)
              }}
            >
              Tôi đã lưu khóa, Đóng
            </button>
          </>
        }
      >
        <StateBanner
          tone="warn"
          message="Khóa này chỉ hiển thị duy nhất một lần ngay lúc cấp và không thể xem lại sau khi đóng hộp thoại. Hệ thống không lưu khóa này vào trình duyệt."
        />
        {issuedKey && <div className="show-once-key">{issuedKey}</div>}
      </Modal>

      {/* Modal xác nhận Thu hồi API Key */}
      <ConfirmDialog
        open={revokeTarget !== null}
        title={`Thu hồi khóa #${revokeTarget?.id ?? ''}`}
        description={
          <p>
            Bạn có chắc muốn thu hồi khóa API <strong>#{revokeTarget?.id}</strong> của Gateway{' '}
            <span className="mono">{selected?.gateway_id}</span>? Các yêu cầu ingestion sử dụng khóa
            này sau khi thu hồi sẽ bị từ chối ngay lập tức.
          </p>
        }
        confirmLabel="Thu hồi khóa"
        tone="danger"
        busy={busy}
        onConfirm={() => {
          if (revokeTarget) {
            void revokeCredential(revokeTarget.id)
          }
        }}
        onCancel={() => setRevokeTarget(null)}
      />

      {/* Modal xác nhận bật/tắt tiếp nhận hàng loạt trên Gateway */}
      <ConfirmDialog
        open={receivingConfirmTarget !== null}
        title={
          receivingConfirmTarget?.nextEnabled
            ? `Bật tiếp nhận cho Gateway ${receivingConfirmTarget.profile.gateway_id ?? ''}`
            : `Tắt tiếp nhận cho Gateway ${receivingConfirmTarget?.profile.gateway_id ?? ''}`
        }
        description={
          <p>
            Thao tác này sẽ{' '}
            <strong>
              {receivingConfirmTarget?.nextEnabled ? 'BẬT' : 'TẮT'}
            </strong>{' '}
            quyền tiếp nhận dữ liệu mới trên Gateway{' '}
            <span className="mono">{receivingConfirmTarget?.profile.gateway_id}</span> và{' '}
            <strong>
              {receivingConfirmTarget?.profile.nodes?.length ?? 0} Node liên kết
            </strong>{' '}
            trong cùng transaction SQL Server.
            {!receivingConfirmTarget?.nextEnabled &&
              ' Mọi timeout period và cảnh báo OPEN của các Node liên kết sẽ được đóng ngay.'}
          </p>
        }
        confirmLabel={receivingConfirmTarget?.nextEnabled ? 'Xác nhận bật' : 'Xác nhận tắt'}
        tone={receivingConfirmTarget?.nextEnabled ? 'primary' : 'danger'}
        busy={busy}
        onConfirm={() => {
          if (receivingConfirmTarget) {
            void toggleReceiving(receivingConfirmTarget.profile, receivingConfirmTarget.nextEnabled)
          }
        }}
        onCancel={() => setReceivingConfirmTarget(null)}
      />
    </div>
  )
}
