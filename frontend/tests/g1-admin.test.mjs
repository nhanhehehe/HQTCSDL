import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const clientSource = await readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const loaderSource = await readFile(
  new URL('../src/features/areas/loader.ts', import.meta.url),
  'utf8',
)

function moduleUrl(source) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText
  return `data:text/javascript;base64,${Buffer.from(compiled + `\n//${Math.random()}`).toString('base64')}`
}

async function loadModules() {
  const clientUrl = moduleUrl(clientSource)
  const client = await import(clientUrl)
  const loader = await import(
    moduleUrl(loaderSource.replace("'../../api/client'", JSON.stringify(clientUrl)))
  )
  return { ...client, ...loader }
}

test('G1 area archive 409 AREA_IN_USE and gateway credential issue/revoke contract', async () => {
  const { requestJson } = await loadModules()
  const realFetch = globalThis.fetch
  const calls = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      calls.push({ url, method: init.method ?? 'GET', headers: init.headers, body: init.body })
      if (url === '/api/v1/farms/1/archive') {
        return new Response(
          JSON.stringify({ error: { code: 'AREA_IN_USE', message: 'Cần tắt quyền nhận (FL-04)' } }),
          { status: 409, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url === '/api/v1/gateways/10/credentials') {
        return new Response(
          JSON.stringify({
            credential: {
              id: '5',
              gateway_id: '10',
              created_at: '2026-11-18T12:00:00.000Z',
              revoked_at: null,
              status: 'ACTIVE',
            },
            api_key: 'hqgw_v1_' + 'A'.repeat(43),
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    await assert.rejects(
      requestJson('/api/v1/farms/1/archive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-HQ-Client': 'web', 'X-CSRF-Token': 'c' },
        body: JSON.stringify({ expected_row_version: 'AAAAAAAAAAE=' }),
      }),
      err => err.status === 409 && err.code === 'AREA_IN_USE',
    )

    const issued = await requestJson('/api/v1/gateways/10/credentials', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-HQ-Client': 'web', 'X-CSRF-Token': 'c' },
      body: JSON.stringify({}),
    })
    assert.equal(issued.credential.status, 'ACTIVE')
    assert.equal(issued.api_key.length, 51)
    assert.equal(calls[1].headers['X-HQ-Client'], 'web')
    assert.equal(calls[1].body, '{}')
  } finally {
    globalThis.fetch = realFetch
  }
})

test('401 on G1 metrics request triggers unauthorized callback and invalidates stale requests', async () => {
  const { requestJson, invalidateRequests, onUnauthorized, StaleResponse } = await loadModules()
  const realFetch = globalThis.fetch
  let cleared = 0
  const unsub = onUnauthorized(() => {
    cleared += 1
  })
  try {
    globalThis.fetch = async url => {
      if (url === '/api/v1/metrics') {
        return new Response(JSON.stringify({ error: { code: 'SESSION_INVALID' } }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    await assert.rejects(requestJson('/api/v1/metrics'), err => err.status === 401)
    assert.equal(cleared, 1)

    const pending = requestJson('/api/v1/farms')
    invalidateRequests()
    await assert.rejects(pending, StaleResponse)
  } finally {
    unsub()
    globalThis.fetch = realFetch
  }
})

test('FarmZoneLoader follows multi-page Farm/Zone cursors and drops stale Zone responses on Farm switch', async () => {
  const { FarmZoneLoader } = await loadModules()
  const realFetch = globalThis.fetch
  const loader = new FarmZoneLoader()
  const urls = []

  let resolveFarm1ZonePage1
  const farm1ZonePage1Deferred = new Promise(resolve => {
    resolveFarm1ZonePage1 = resolve
  })

  try {
    globalThis.fetch = async (url, init = {}) => {
      urls.push(url)
      if (url === '/api/v1/farms?status=ACTIVE&page_size=10') {
        return new Response(
          JSON.stringify({
            items: [{ id: '1', code: 'F-01', name: 'Farm 1' }],
            next_cursor: 'cur-f-1',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url === '/api/v1/farms?status=ACTIVE&page_size=10&cursor=cur-f-1') {
        return new Response(
          JSON.stringify({
            items: [{ id: '2', code: 'F-02', name: 'Farm 2' }],
            next_cursor: null,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url === '/api/v1/farms/1/zones?status=ACTIVE&page_size=10') {
        await farm1ZonePage1Deferred
        if (init.signal?.aborted) {
          throw new DOMException('Aborted', 'AbortError')
        }
        return new Response(
          JSON.stringify({
            items: [{ id: '11', farm_id: '1', code: 'Z-11', name: 'Zone 11' }],
            next_cursor: 'cur-z-11',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url === '/api/v1/farms/2/zones?status=ACTIVE&page_size=10') {
        return new Response(
          JSON.stringify({
            items: [{ id: '21', farm_id: '2', code: 'Z-21', name: 'Zone 21' }],
            next_cursor: 'cur-z-21',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url === '/api/v1/farms/2/zones?status=ACTIVE&page_size=10&cursor=cur-z-21') {
        return new Response(
          JSON.stringify({
            items: [{ id: '22', farm_id: '2', code: 'Z-22', name: 'Zone 22' }],
            next_cursor: null,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      throw new Error(`Unexpected URL: ${url}`)
    }

    // 1. Multi-page Farm loading via next_cursor
    const farms = await loader.loadActiveFarms()
    assert.deepEqual(
      farms.map(f => f.code),
      ['F-01', 'F-02'],
    )

    // 2. Select Farm 1 (in-flight), then switch to Farm 2 before Farm 1 finishes
    let selectedFarmId = ''
    let zones = []
    let selectedZoneId = 'old-zone'
    const loadedHistory = []

    const callbacks = {
      onReset: farmId => {
        selectedFarmId = farmId
        zones = []
        selectedZoneId = ''
      },
      onLoaded: nextZones => {
        zones = nextZones
        loadedHistory.push(nextZones.map(z => z.code))
      },
      onError: () => {
        throw new Error('Should not report error for aborted Farm 1 request')
      },
    }

    const p1 = loader.selectFarm('1', callbacks)
    assert.equal(selectedFarmId, '1')
    assert.equal(selectedZoneId, '')

    const p2 = loader.selectFarm('2', callbacks)
    assert.equal(selectedFarmId, '2')

    // Let Farm 1's delayed page 1 resolve after switching to Farm 2
    resolveFarm1ZonePage1()
    await Promise.all([p1, p2])

    // Farm 1's response must be dropped (and page 2 of Farm 1 never requested), while Farm 2's 2 pages load
    assert.deepEqual(
      zones.map(z => z.code),
      ['Z-21', 'Z-22'],
    )
    assert.deepEqual(loadedHistory, [['Z-21', 'Z-22']])
    assert.equal(
      urls.includes('/api/v1/farms/1/zones?status=ACTIVE&page_size=10&cursor=cur-z-11'),
      false,
    )
  } finally {
    loader.dispose()
    globalThis.fetch = realFetch
  }
})
