import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const clientSource = await readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const formatSource = await readFile(
  new URL('../src/features/measurements/format.ts', import.meta.url),
  'utf8',
)
const scopeLoaderSource = await readFile(
  new URL('../src/features/measurements/scopeLoader.ts', import.meta.url),
  'utf8',
)
const measStoreSource = await readFile(
  new URL('../src/features/measurements/store.ts', import.meta.url),
  'utf8',
)
const dashCoordinatorSource = await readFile(
  new URL('../src/features/dashboard/coordinator.ts', import.meta.url),
  'utf8',
)
const reportsStoreSource = await readFile(
  new URL('../src/features/reports/store.ts', import.meta.url),
  'utf8',
)

function moduleUrl(source) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText
  return `data:text/javascript;base64,${Buffer.from(compiled + `\n// ${Math.random()}`).toString('base64')}`
}

async function loadG3Modules() {
  const clientUrl = moduleUrl(clientSource)
  const formatUrl = moduleUrl(formatSource)
  const scopeUrl = moduleUrl(scopeLoaderSource.replace("'../../api/client'", JSON.stringify(clientUrl)))
  const measUrl = moduleUrl(
    measStoreSource
      .replace("'../../api/client'", JSON.stringify(clientUrl))
      .replace("'./format'", JSON.stringify(formatUrl))
      .replace("'./scopeLoader'", JSON.stringify(scopeUrl)),
  )
  const dashUrl = moduleUrl(
    dashCoordinatorSource
      .replace("'../../api/client'", JSON.stringify(clientUrl))
      .replace("'../measurements/scopeLoader'", JSON.stringify(scopeUrl)),
  )
  const reportsUrl = moduleUrl(
    reportsStoreSource
      .replace("'../../api/client'", JSON.stringify(clientUrl))
      .replace("'../measurements/format'", JSON.stringify(formatUrl))
      .replace("'../measurements/scopeLoader'", JSON.stringify(scopeUrl)),
  )

  const client = await import(clientUrl)
  const format = await import(formatUrl)
  const meas = await import(measUrl)
  const dash = await import(dashUrl)
  const reports = await import(reportsUrl)
  return { ...client, ...format, ...meas, ...dash, ...reports }
}

test('format helpers keep fixed 5-metric order and explicit UTC+07:00 conversion', async () => {
  const { METRIC_META, formatUtcToVi, viDatetimeInputToIso, utcIsoToViDatetimeInput, freshnessBadge } =
    await loadG3Modules()

  assert.deepEqual(
    METRIC_META.map(m => m.code),
    ['temperature', 'airHumidity', 'pressure', 'light', 'soilMoisture'],
  )
  assert.equal(formatUtcToVi('2026-11-15T02:00:00.000Z'), '09:00:00 15/11/2026 (UTC+07:00)')
  assert.equal(utcIsoToViDatetimeInput('2026-11-15T02:00:00.000Z'), '2026-11-15T09:00')
  assert.equal(viDatetimeInputToIso('2026-11-15T09:00'), '2026-11-15T09:00:00+07:00')
  assert.equal(freshnessBadge('CLOCK_AHEAD').label, 'Giờ mẫu vượt giờ Backend')
  assert.equal(freshnessBadge('PROFILE_MISSING').label, 'Thiếu profile thời gian')
})

test('DashboardCoordinator enforces single-flight 5s poll, stale retention on 503, dataEpoch race, and 401 clear', async () => {
  const { DashboardCoordinator, DASHBOARD_POLL_INTERVAL_MS, DASHBOARD_REQUEST_TIMEOUT_MS } =
    await loadG3Modules()
  const realFetch = globalThis.fetch
  let nowMs = 1000
  const timers = new Map()
  let nextTimerId = 1

  const coord = new DashboardCoordinator({
    now: () => nowMs,
    setTimer: (fn, delayMs) => {
      const id = nextTimerId++
      timers.set(id, { fn, delayMs })
      return id
    },
    clearTimer: id => {
      timers.delete(id)
    },
    isHidden: () => false,
  })

  try {
    let releaseFirst
    const firstPending = new Promise(resolve => {
      releaseFirst = resolve
    })
    let callCount = 0

    globalThis.fetch = async (url, init = {}) => {
      callCount += 1
      assert.equal(url, '/api/v1/dashboard?farm_id=1')
      if (callCount === 1) {
        await firstPending
        if (init.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
        return new Response(
          JSON.stringify({
            as_of: '2026-11-15T10:00:00.000Z',
            capabilities: { monitoring: false, alerts: false },
            scope: {
              kind: 'FARM',
              farm_id: '1',
              farm: { id: '1', code: 'F1', name: 'Farm 1', archived_at: null },
              node_ids: ['101'],
            },
            active_count: null,
            nodes: [
              {
                profile: {
                  id: '101',
                  device_id: 'OLD-NODE',
                  name: 'Old',
                  zone: { id: '10', code: 'Z1', name: 'Zone 1', archived_at: null },
                  gateway: null,
                  receiving_enabled: true,
                },
                current: { state: 'NO_DATA', measurement: null, as_of: '2026-11-15T10:00:00.000Z' },
                freshness: { state: 'NO_DATA', age_seconds: null, stale_after_seconds: null },
                monitoring: null,
                evaluation: null,
                active_alerts: null,
                active_count: null,
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (callCount === 2) {
        return new Response(
          JSON.stringify({
            as_of: '2026-11-15T10:00:05.000Z',
            capabilities: { monitoring: false, alerts: false },
            scope: {
              kind: 'FARM',
              farm_id: '1',
              farm: { id: '1', code: 'F1', name: 'Farm 1', archived_at: null },
              node_ids: ['101'],
            },
            active_count: null,
            nodes: [
              {
                profile: {
                  id: '101',
                  device_id: 'NEW-NODE',
                  name: 'New',
                  zone: { id: '10', code: 'Z1', name: 'Zone 1', archived_at: null },
                  gateway: null,
                  receiving_enabled: true,
                },
                current: { state: 'NO_DATA', measurement: null, as_of: '2026-11-15T10:00:05.000Z' },
                freshness: { state: 'NO_DATA', age_seconds: null, stale_after_seconds: null },
                monitoring: null,
                evaluation: null,
                active_alerts: null,
                active_count: null,
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (callCount === 3) {
        return new Response(
          JSON.stringify({ error: { code: 'STORAGE_UNAVAILABLE', message: 'SQL down' } }),
          { status: 503, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(
        JSON.stringify({ error: { code: 'SESSION_INVALID', message: 'Expired' } }),
        { status: 401, headers: { 'Content-Type': 'application/json' } },
      )
    }

    assert.equal(DASHBOARD_POLL_INTERVAL_MS, 5000)
    assert.equal(DASHBOARD_REQUEST_TIMEOUT_MS, 4000)

    coord.setFarm('1')
    // Single-flight: extra fetchOnce while busy does NOT issue a second fetch
    await coord.fetchOnce()
    assert.equal(callCount, 1)

    const flush = () => new Promise(resolve => setTimeout(resolve, 0))

    // refreshNow bumps dataEpoch and aborts call 1, starting call 2
    coord.refreshNow()
    releaseFirst()
    await flush()

    assert.equal(callCount, 2)
    assert.equal(coord.getSnapshot().snapshot?.nodes[0].profile.device_id, 'NEW-NODE')
    assert.equal(coord.getSnapshot().showingStale, false)

    // Verify start-to-start 5000ms timer scheduled
    assert.equal(timers.size, 1)
    const [timerEntry] = [...timers.values()]
    assert.equal(timerEntry.delayMs, 5000)

    // Trigger next poll -> 503 keeps previous snapshot with showingStale=true
    nowMs += 5000
    timers.clear()
    timerEntry.fn()
    await flush()
    assert.equal(callCount, 3)
    assert.equal(coord.getSnapshot().showingStale, true)
    assert.equal(coord.getSnapshot().snapshot?.nodes[0].profile.device_id, 'NEW-NODE')

    // Hide pauses timer
    coord.setVisibility(true)
    assert.equal(timers.size, 0)

    // Visible again triggers immediate fetch -> 401 clears private snapshot completely and stops polling
    coord.setVisibility(false)
    await flush()
    assert.equal(callCount, 4)
    assert.equal(coord.getSnapshot().snapshot, null)
    assert.equal(coord.getSnapshot().showingStale, false)
    assert.equal(timers.size, 0)
  } finally {
    coord.dispose()
    globalThis.fetch = realFetch
  }
})

test('MeasurementsStore isolates current from history filter and drops stale responses on Farm/Node switch', async () => {
  const { MeasurementsStore } = await loadG3Modules()
  const realFetch = globalThis.fetch
  const store = new MeasurementsStore(Date.UTC(2026, 10, 15, 10, 0, 0))
  const requested = []

  try {
    let releaseOldHistory
    const oldHistoryPending = new Promise(resolve => {
      releaseOldHistory = resolve
    })

    globalThis.fetch = async (url, init = {}) => {
      requested.push(url)
      if (url.includes('/nodes/1/measurements/current')) {
        return new Response(
          JSON.stringify({ state: 'NO_DATA', measurement: null, as_of: '2026-11-15T10:00:00.000Z' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.includes('/nodes/1/measurements?time_mode=known')) {
        await oldHistoryPending
        if (init.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
        return new Response(
          JSON.stringify({
            state: 'AVAILABLE',
            items: [{ id: '999' }],
            next_cursor: null,
            view_token: 'v-old',
            range: null,
            as_of: '2026-11-15T10:00:00.000Z',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url === '/api/v1/nodes/1/measurements?time_mode=unknown&page_size=50') {
        return new Response(
          JSON.stringify({
            state: 'AVAILABLE',
            items: [{ id: '201' }],
            next_cursor: 'cur-u-2',
            view_token: 'vt-u',
            range: null,
            as_of: '2026-11-15T10:00:00.000Z',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (
        url ===
        '/api/v1/nodes/1/measurements?time_mode=unknown&page_size=50&cursor=cur-u-2&view_token=vt-u'
      ) {
        return new Response(
          JSON.stringify({
            state: 'AVAILABLE',
            items: [{ id: '200' }],
            next_cursor: null,
            view_token: 'vt-u',
            range: null,
            as_of: '2026-11-15T10:00:00.000Z',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      throw new Error(`Unexpected URL: ${url}`)
    }

    store.setNodeId('1')
    await store.loadCurrent()
    const knownPromise = store.loadHistory(false)
    // Switch to unknown mode before old known response returns (must NOT reset current.data!)
    store.setTimeMode('unknown')
    const unknownPromise = store.loadHistory(false)
    releaseOldHistory()
    await Promise.all([knownPromise, unknownPromise])

    assert.equal(store.getSnapshot().current.data?.state, 'NO_DATA')
    assert.deepEqual(
      store.getSnapshot().history.items.map(i => i.id),
      ['201'],
    )
    assert.equal(store.getSnapshot().history.viewToken, 'vt-u')

    // Unknown mode never sends from/to and passes view_token on loadMore
    await store.loadHistory(true)
    assert.deepEqual(
      store.getSnapshot().history.items.map(i => i.id),
      ['201', '200'],
    )
    assert.equal(
      requested.some(u => u.includes('time_mode=unknown') && u.includes('from=')),
      false,
    )
  } finally {
    store.dispose()
    globalThis.fetch = realFetch
  }
})

test('ReportsStore enforces draft vs result dirty lock, 410 manual retry, and stale response drop', async () => {
  const { ReportsStore } = await loadG3Modules()
  const realFetch = globalThis.fetch
  const store = new ReportsStore(Date.UTC(2026, 10, 15, 10, 0, 0))

  try {
    let releaseFirstCreate
    const firstCreatePending = new Promise(resolve => {
      releaseFirstCreate = resolve
    })
    let createCalls = 0

    globalThis.fetch = async (url, init = {}) => {
      if (url === '/api/v1/reports/measurements') {
        createCalls += 1
        if (createCalls === 1) {
          await firstCreatePending
          if (init.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
          return new Response(JSON.stringify({ N_total: '999', snapshot_token: 'old-tok' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        return new Response(
          JSON.stringify({
            context: {
              source: 'test',
              farm: { id: '1', code: 'F1', name: 'Farm 1' },
              zone: { id: '10', code: 'Z1', name: 'Zone 1' },
              node: { id: '101', device_id: 'NODE-01', name: 'Node 1' },
              from: '2026-11-14T10:00:00.000Z',
              to: '2026-11-15T10:00:00.000Z',
              timezone: 'UTC+07:00',
            },
            as_of: '2026-11-15T10:05:00.000Z',
            N_total: '5',
            rows: [],
            snapshot_token: 'tok-fresh',
            expires_at: '2026-11-15T10:20:00.000Z',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url === '/api/v1/reports/measurements/csv') {
        return new Response(
          JSON.stringify({ error: { code: 'SNAPSHOT_EXPIRED', message: 'Expired' } }),
          { status: 410, headers: { 'Content-Type': 'application/json' } },
        )
      }
      throw new Error(`Unexpected URL: ${url}`)
    }

    store.setNodeId('101')
    assert.equal(store.canExportCsv(true), false)

    // Start first create, then start second create before first settles
    const p1 = store.createReport('csrf-1', true)
    const p2 = store.createReport('csrf-1', true)
    releaseFirstCreate()
    await Promise.all([p1, p2])

    assert.equal(store.getSnapshot().result?.snapshot_token, 'tok-fresh')
    assert.equal(store.canExportCsv(true), true)

    // Dirtying draft immediately disables CSV export while preserving old result for reference
    store.setFromInput('2026-11-14T00:00')
    assert.equal(store.canExportCsv(true), false)
    assert.equal(store.getSnapshot().result?.snapshot_token, 'tok-fresh')
    assert.equal(store.getSnapshot().resultMatchesDraft, false)

    // Re-create report to unlock CSV, then trigger 410 SNAPSHOT_EXPIRED -> MUST NOT auto-create
    await store.createReport('csrf-1', true)
    assert.equal(store.canExportCsv(true), true)
    const callsBeforeExport = createCalls
    const csvRes = await store.exportCsv('csrf-1', true)
    assert.equal(csvRes, null)
    assert.equal(createCalls, callsBeforeExport)
    assert.match(store.getSnapshot().exportError, /410 SNAPSHOT_EXPIRED/)
  } finally {
    store.dispose()
    globalThis.fetch = realFetch
  }
})
