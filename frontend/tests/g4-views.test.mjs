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
const monitoringApiSource = await readFile(new URL('../src/api/monitoring.ts', import.meta.url), 'utf8')
const monitoringStoreSource = await readFile(
  new URL('../src/features/monitoring/store.ts', import.meta.url),
  'utf8',
)
const alertsApiSource = await readFile(new URL('../src/api/alerts.ts', import.meta.url), 'utf8')
const alertsStoreSource = await readFile(
  new URL('../src/features/alerts/store.ts', import.meta.url),
  'utf8',
)
const dashG4HelpersSource = await readFile(
  new URL('../src/features/dashboard/g4Helpers.ts', import.meta.url),
  'utf8',
)

function moduleUrl(source) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText
  return `data:text/javascript;base64,${Buffer.from(compiled + `\n// ${Math.random()}`).toString('base64')}`
}

async function loadG4Modules() {
  const clientUrl = moduleUrl(clientSource)
  const formatUrl = moduleUrl(formatSource)
  const scopeUrl = moduleUrl(scopeLoaderSource.replace("'../../api/client'", JSON.stringify(clientUrl)))
  const monApiUrl = moduleUrl(
    monitoringApiSource.replace("'./client'", JSON.stringify(clientUrl)),
  )
  const monStoreUrl = moduleUrl(
    monitoringStoreSource
      .replace("'../../api/client'", JSON.stringify(clientUrl))
      .replace("'../../api/monitoring'", JSON.stringify(monApiUrl))
      .replace("from '../../api/monitoring'", `from ${JSON.stringify(monApiUrl)}`),
  )
  const alertsApiUrl = moduleUrl(
    alertsApiSource.replace("'./client'", JSON.stringify(clientUrl)),
  )
  const alertsStoreUrl = moduleUrl(
    alertsStoreSource
      .replace("'../../api/alerts'", JSON.stringify(alertsApiUrl))
      .replace("'../measurements/scopeLoader'", JSON.stringify(scopeUrl))
      .replace("'../measurements/format'", JSON.stringify(formatUrl)),
  )
  const dashG4Url = moduleUrl(dashG4HelpersSource)

  const client = await import(clientUrl)
  const monApi = await import(monApiUrl)
  const monStore = await import(monStoreUrl)
  const alertsApi = await import(alertsApiUrl)
  const alertsStore = await import(alertsStoreUrl)
  const dashG4 = await import(dashG4Url)
  return { ...client, ...monApi, ...monStore, ...alertsApi, ...alertsStore, ...dashG4 }
}

function makeMonitoringSnapshot(overrides = {}) {
  return {
    node_id: 'node-1',
    threshold_config_id: 'cfg-th-1',
    timeout_config_id: 'cfg-to-1',
    thresholds: [
      { metric_code: 'temperature', enabled: true, lower: '18.50', upper: '32.75', state: 'ACTIVE' },
      { metric_code: 'airHumidity', enabled: false, lower: null, upper: null, state: 'UNCONFIGURED' },
      { metric_code: 'pressure', enabled: false, lower: null, upper: null, state: 'UNCONFIGURED' },
      { metric_code: 'light', enabled: false, lower: null, upper: null, state: 'UNCONFIGURED' },
      { metric_code: 'soilMoisture', enabled: false, lower: null, upper: null, state: 'UNCONFIGURED' },
    ],
    timeout_minutes: 15,
    timeout_state: 'ACTIVE',
    S: '300',
    L: '60',
    active_timeout_period_id: '10',
    evaluation: {
      desired_generation: '5',
      completed_generation: '5',
      status: 'UP_TO_DATE',
      last_success_at: '2026-11-21T10:00:00Z',
      last_error_code: null,
    },
    timeout_validation: {
      state: 'READY',
      sampling_interval_seconds: 300,
      normal_delivery_budget_seconds: 60,
      min_timeout_minutes: 12,
    },
    ...overrides,
  }
}

test('Monitoring decimal validation and raw JSON serialization avoid binary float coercion', async () => {
  const {
    isValidExactDecimal92,
    serializeMonitoringPatchJson,
    buildAtomicMonitoringPatchPayload,
    createDraftFromMonitoring,
  } = await loadG4Modules()

  assert.equal(isValidExactDecimal92('18.55'), true)
  assert.equal(isValidExactDecimal92('-40'), true)
  assert.equal(isValidExactDecimal92('9999999.99'), true)
  assert.equal(isValidExactDecimal92('18.555'), false)
  assert.equal(isValidExactDecimal92('018.50'), false)
  assert.equal(isValidExactDecimal92('NaN'), false)

  const mon = makeMonitoringSnapshot()
  const { thresholds, timeoutMinutes } = createDraftFromMonitoring(mon)
  thresholds.temperature = { metric_code: 'temperature', enabled: true, lower: '19.99', upper: '34.01' }
  thresholds.airHumidity = { metric_code: 'airHumidity', enabled: false, lower: '', upper: '' }

  const res = buildAtomicMonitoringPatchPayload({
    includeThresholds: true,
    includeTimeout: true,
    mon,
    drafts: thresholds,
    timeoutInput: timeoutMinutes,
    metricsCatalog: [],
  })
  assert.equal(res.ok, true)
  const jsonText = serializeMonitoringPatchJson(res.payload)
  assert.match(jsonText, /"lower":19\.99,"upper":34\.01/)
  assert.match(jsonText, /"timeout_minutes":15/)

  // Half-deleted pair is rejected
  const badPair = {
    ...thresholds,
    pressure: { metric_code: 'pressure', enabled: false, lower: '950', upper: '' },
  }
  const resHalf = buildAtomicMonitoringPatchPayload({
    includeThresholds: true,
    includeTimeout: false,
    mon,
    drafts: badPair,
    timeoutInput: timeoutMinutes,
    metricsCatalog: [],
  })
  assert.equal(resHalf.ok, false)
})

test('PROFILE_MISSING locks timeout T updates while still allowing threshold updates', async () => {
  const { buildAtomicMonitoringPatchPayload, createDraftFromMonitoring } = await loadG4Modules()

  const mon = makeMonitoringSnapshot({
    timeout_minutes: null,
    timeout_state: 'UNCONFIGURED',
    timeout_validation: {
      state: 'PROFILE_MISSING',
      sampling_interval_seconds: null,
      normal_delivery_budget_seconds: null,
      min_timeout_minutes: null,
    },
  })
  const { thresholds } = createDraftFromMonitoring(mon)

  const onlyThresholds = buildAtomicMonitoringPatchPayload({
    includeThresholds: true,
    includeTimeout: false,
    mon,
    drafts: thresholds,
    timeoutInput: '15',
    metricsCatalog: [],
  })
  assert.equal(onlyThresholds.ok, true)

  const withTimeout = buildAtomicMonitoringPatchPayload({
    includeThresholds: true,
    includeTimeout: true,
    mon,
    drafts: thresholds,
    timeoutInput: '15',
    metricsCatalog: [],
  })
  assert.equal(withTimeout.ok, false)
  assert.match(withTimeout.error, /PROFILE_MISSING/)
})

test('formatMonitoringApiError displays 422 details.reason and details.field without echoing raw invalid values', async () => {
  const { ApiError, formatMonitoringApiError } = await loadG4Modules()

  const err422 = new ApiError(422, 'req-422', 'THRESHOLD_DECIMAL_INVALID', 'Invalid decimal', {
    reason: 'DECIMAL_PRECISION_EXCEEDED',
    field: 'thresholds.temperature.lower',
  })
  const text = formatMonitoringApiError(err422, 'fallback')
  assert.equal(text, 'DECIMAL_PRECISION_EXCEEDED (thresholds.temperature.lower): Invalid decimal')

  const err409 = new ApiError(409, 'req-409', 'CONFIG_VERSION_CONFLICT', 'Stale')
  assert.match(formatMonitoringApiError(err409, 'fallback'), /409 CONFIG_VERSION_CONFLICT/)
})

test('Alerts filter validator enforces FL-11 rules and AlertsStore prevents stale abort overwrites', async () => {
  const {
    AlertsStore,
    buildAlertsListParams,
    createDefaultAlertFilterDraft,
    validateAlertFilterDraft,
  } = await loadG4Modules()

  const badActiveClosed = { ...createDefaultAlertFilterDraft('active'), lifecycle: 'CLOSED' }
  assert.equal(validateAlertFilterDraft(badActiveClosed).ok, false)

  const badTimeoutRecovery = {
    ...createDefaultAlertFilterDraft('history'),
    kind: 'TIMEOUT',
    recovery_proven: 'true',
  }
  assert.equal(validateAlertFilterDraft(badTimeoutRecovery).ok, false)

  const validDraft = {
    ...createDefaultAlertFilterDraft('history', 'node-9'),
    kind: 'THRESHOLD',
    acknowledged: 'false',
    fromLocal: '2026-11-20T09:00',
    toLocal: '2026-11-21T09:00',
  }
  const v = validateAlertFilterDraft(validDraft)
  assert.equal(v.ok, true)
  const params = buildAlertsListParams('farm-1', validDraft, v, { pageSize: 20 })
  assert.equal(params.farm_id, 'farm-1')
  assert.equal('metric_code' in params, false)

  // Verify AbortController race: slow 1st load is aborted when 2nd load runs
  let abortCount = 0
  let callIndex = 0
  const store = new AlertsStore(async (p, signal) => {
    callIndex += 1
    const idx = callIndex
    if (idx === 1) {
      return await new Promise((_, reject) => {
        signal?.addEventListener('abort', () => {
          abortCount += 1
          reject(new DOMException('Aborted', 'AbortError'))
        })
      })
    }
    return {
      as_of: '2026-11-21T10:00:00Z',
      range: { from: null, to: null },
      evaluation_by_node: {
        'node-1': {
          desired_generation: '2',
          completed_generation: '2',
          status: 'UP_TO_DATE',
          last_success_at: '2026-11-21T10:00:00Z',
          last_error_code: null,
        },
      },
      items: [
        {
          id: `ep-${p.view}`,
          node_id: 'node-1',
          device_id: 'NODE-01',
          node_name: 'Node 1',
          zone_id: 'z-1',
          zone_code: 'Z1',
          farm_id: 'farm-1',
          kind: 'THRESHOLD',
          metric_code: 'temperature',
          direction: 'HIGH',
          lifecycle_status: 'OPEN',
          is_current: true,
          current_revision_no: 1,
          acknowledgement_version: 0,
          acknowledged: false,
          current_acknowledgement: null,
          recovery_proven: null,
          first_detected_at: '2026-11-21T09:50:00Z',
          closed_at: null,
          end_reason: null,
          current_revision: {
            revision_no: 1,
            config_id: 'cfg-1',
            period_id: null,
            direction: 'HIGH',
            start_event_time: '2026-11-21T09:50:00Z',
            end_event_time: null,
            start_backend_at: '2026-11-21T09:50:01Z',
            end_backend_at: null,
            closed_at: null,
            end_reason: null,
            start_measurement_id: 'm1',
            end_measurement_id: null,
            lower_snapshot: '10.00',
            upper_snapshot: '30.00',
            timeout_snapshot: null,
            evaluated_at: '2026-11-21T09:50:02Z',
            evaluation_generation: '2',
            acknowledgement_version: 0,
            threshold_metadata: null,
          },
        },
      ],
      next_cursor: 'cursor-2',
    }
  })

  const firstLoad = store.load({ farm_id: 'farm-1', view: 'active' })
  const secondLoad = store.load({ farm_id: 'farm-1', view: 'history' })
  await Promise.all([firstLoad, secondLoad])
  assert.equal(abortCount, 1)
  assert.equal(store.getState().items[0].id, 'ep-history')
  assert.equal(store.getState().error, '')
})

test('Dashboard G4 helpers preserve Farm-wide active_alerts regardless of left Node focus', async () => {
  const {
    collectFarmActiveAlerts,
    countFarmActiveAlerts,
    findNewlyTriggeredAlerts,
    summarizeFarmEvaluation,
  } = await loadG4Modules()

  const alertN1 = {
    id: '101',
    node_id: 'n1',
    device_id: 'N-01',
    node_name: 'Node 1',
    zone_id: 'z1',
    zone_code: 'Z1',
    farm_id: 'f1',
    kind: 'THRESHOLD',
    metric_code: 'temperature',
    direction: 'HIGH',
    lifecycle_status: 'OPEN',
    is_current: true,
    current_revision_no: 1,
    acknowledgement_version: 0,
    acknowledged: true,
    current_acknowledgement: null,
    recovery_proven: null,
    first_detected_at: '2026-11-21T09:00:00Z',
    closed_at: null,
    end_reason: null,
    current_revision: {},
  }
  const alertN2 = {
    ...alertN1,
    id: '102',
    node_id: 'n2',
    device_id: 'N-02',
    node_name: 'Node 2',
    acknowledged: false,
    first_detected_at: '2026-11-21T09:30:00Z',
  }

  const g4View = {
    as_of: '2026-11-21T10:00:00Z',
    capabilities: { monitoring: true, alerts: true },
    scope: { kind: 'FARM', farm_id: 'f1', farm: { id: 'f1', code: 'F1', name: 'Farm 1', archived_at: null }, node_ids: ['n1', 'n2'] },
    active_count: '2',
    nodes: [
      {
        profile: { id: 'n1' },
        current: {},
        freshness: { state: 'FRESH' },
        monitoring: makeMonitoringSnapshot({ node_id: 'n1' }),
        evaluation: { desired_generation: '3', completed_generation: '3', status: 'UP_TO_DATE', last_success_at: null, last_error_code: null },
        active_alerts: [alertN1],
        active_count: '1',
      },
      {
        profile: { id: 'n2' },
        current: {},
        freshness: { state: 'STALE' },
        monitoring: makeMonitoringSnapshot({ node_id: 'n2' }),
        evaluation: { desired_generation: '4', completed_generation: '3', status: 'PENDING', last_success_at: null, last_error_code: null },
        active_alerts: [alertN2],
        active_count: '1',
      },
    ],
  }

  assert.equal(countFarmActiveAlerts(g4View), 2)
  const collected = collectFarmActiveAlerts(g4View)
  assert.deepEqual(
    collected.map(a => a.id),
    ['102', '101'], // unacknowledged comes before acknowledged for OPEN alerts
  )
  assert.deepEqual(summarizeFarmEvaluation(g4View), {
    enabled: true,
    total: 2,
    upToDate: 1,
    pending: 1,
    error: 0,
  })
  assert.deepEqual(
    findNewlyTriggeredAlerts(new Set(['101']), collected).map(a => a.id),
    ['102'],
  )

  // Honest null boundary when G3 capabilities are false
  const g3View = {
    ...g4View,
    capabilities: { monitoring: false, alerts: false },
    active_count: null,
    nodes: g4View.nodes.map(n => ({
      ...n,
      monitoring: null,
      evaluation: null,
      active_alerts: null,
      active_count: null,
    })),
  }
  assert.equal(countFarmActiveAlerts(g3View), null)
  assert.deepEqual(collectFarmActiveAlerts(g3View), [])
})
