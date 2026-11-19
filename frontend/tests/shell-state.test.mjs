import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const clientSource = await readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const navigationSource = await readFile(
  new URL('../src/app/navigation.ts', import.meta.url),
  'utf8',
)
const storeSource = await readFile(
  new URL('../src/app/operationalFarmStore.ts', import.meta.url),
  'utf8',
)

function moduleUrl(source) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText
  return `data:text/javascript;base64,${Buffer.from(compiled + `\n// ${Math.random()}`).toString('base64')}`
}

async function loadShellModules() {
  const clientUrl = moduleUrl(clientSource)
  const client = await import(clientUrl)
  const nav = await import(moduleUrl(navigationSource))
  const storeMod = await import(
    moduleUrl(storeSource.replace("'../api/client'", JSON.stringify(clientUrl)))
  )
  return { ...client, ...nav, ...storeMod }
}

test('navigation policy defines /dashboard default, operational routes, and all 5 auth view states', async () => {
  const { DEFAULT_AUTHENTICATED_ROUTE, isOperationalRoute, resolveAuthView } =
    await loadShellModules()

  assert.equal(DEFAULT_AUTHENTICATED_ROUTE, '/dashboard')

  for (const path of ['/dashboard', '/measurements', '/reports', '/monitoring', '/alerts', '/alerts/42', '/alerts/']) {
    assert.equal(isOperationalRoute(path), true, `Expected operational route for ${path}`)
  }
  for (const path of ['/areas', '/areas/', '/devices', '/login', '/']) {
    assert.equal(isOperationalRoute(path), false, `Expected non-operational route for ${path}`)
  }

  assert.equal(
    resolveAuthView({ initialized: false, session: null, uncertainLogout: false }),
    'initializing',
  )
  assert.equal(
    resolveAuthView({ initialized: true, session: { admin: { id: '1' } }, uncertainLogout: false }),
    'authenticated',
  )
  assert.equal(
    resolveAuthView({ initialized: true, session: null, uncertainLogout: true }),
    'uncertain_logout',
  )
  assert.equal(
    resolveAuthView({ initialized: true, session: null, uncertainLogout: false }),
    'unauthenticated',
  )
})

test('OperationalFarmStore never auto-selects first farm, follows next_cursor, and tracks contextGeneration', async () => {
  const { OperationalFarmStore } = await loadShellModules()
  const realFetch = globalThis.fetch
  const store = new OperationalFarmStore()

  try {
    assert.equal(store.getSnapshot().selectedFarmId, '')
    assert.equal(store.getSnapshot().contextGeneration, 0)

    const requested = []
    globalThis.fetch = async url => {
      requested.push(url)
      if (url === '/api/v1/farms?status=ALL&page_size=50') {
        return new Response(
          JSON.stringify({
            items: [{ id: '1', code: 'F-01', name: 'Farm 1', description: null, archived_at: null, row_version: 'A' }],
            next_cursor: 'cur-2',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url === '/api/v1/farms?status=ALL&page_size=50&cursor=cur-2') {
        return new Response(
          JSON.stringify({
            items: [{ id: '2', code: 'F-02', name: 'Farm 2', description: null, archived_at: '2026-11-18T00:00:00Z', row_version: 'B' }],
            next_cursor: null,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      throw new Error(`Unexpected URL: ${url}`)
    }

    await store.loadFarms()
    assert.deepEqual(requested, [
      '/api/v1/farms?status=ALL&page_size=50',
      '/api/v1/farms?status=ALL&page_size=50&cursor=cur-2',
    ])
    assert.equal(store.getSnapshot().farms.length, 2)
    assert.equal(store.getSnapshot().selectedFarmId, '')
    assert.equal(store.getSnapshot().contextGeneration, 0)

    store.selectFarm('2')
    assert.equal(store.getSnapshot().selectedFarmId, '2')
    assert.equal(store.getSelectedFarm()?.code, 'F-02')
    assert.equal(store.getSnapshot().contextGeneration, 1)

    // Unknown farm IDs are ignored
    store.selectFarm('999')
    assert.equal(store.getSnapshot().selectedFarmId, '2')
    assert.equal(store.getSnapshot().contextGeneration, 1)

    // Reload where previously selected farm no longer exists resets selection and bumps contextGeneration
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({
          items: [{ id: '1', code: 'F-01', name: 'Farm 1', description: null, archived_at: null, row_version: 'A' }],
          next_cursor: null,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    await store.loadFarms()
    assert.equal(store.getSnapshot().selectedFarmId, '')
    assert.equal(store.getSnapshot().contextGeneration, 2)
  } finally {
    store.dispose()
    globalThis.fetch = realFetch
  }
})

test('OperationalFarmStore drops stale out-of-order responses and cancels on dispose', async () => {
  const { OperationalFarmStore } = await loadShellModules()
  const realFetch = globalThis.fetch
  const store = new OperationalFarmStore()

  let releaseFirst
  const firstPending = new Promise(resolve => {
    releaseFirst = resolve
  })
  const urls = []
  let callIndex = 0

  try {
    globalThis.fetch = async (url, init = {}) => {
      urls.push(url)
      callIndex += 1
      if (callIndex === 1) {
        await firstPending
        if (init.signal?.aborted) {
          throw new DOMException('Aborted', 'AbortError')
        }
        return new Response(
          JSON.stringify({
            items: [{ id: '1', code: 'OLD', name: 'Old Farm', description: null, archived_at: null, row_version: 'A' }],
            next_cursor: 'stale-page-2',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(
        JSON.stringify({
          items: [{ id: '2', code: 'NEW', name: 'New Farm', description: null, archived_at: null, row_version: 'B' }],
          next_cursor: null,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }

    const first = store.loadFarms()
    const second = store.loadFarms()
    await second
    releaseFirst()
    await first

    assert.deepEqual(
      store.getSnapshot().farms.map(f => f.code),
      ['NEW'],
    )
    assert.equal(urls.includes('/api/v1/farms?status=ALL&page_size=50&cursor=stale-page-2'), false)

    // Aborts in-flight load on dispose
    let aborted = false
    let releaseDisposeCheck
    const pendingDispose = new Promise(resolve => {
      releaseDisposeCheck = resolve
    })
    globalThis.fetch = async (_url, init = {}) => {
      init.signal?.addEventListener('abort', () => {
        aborted = true
      })
      await pendingDispose
      throw new DOMException('Aborted', 'AbortError')
    }
    const inflight = store.loadFarms()
    store.dispose()
    releaseDisposeCheck()
    await inflight
    assert.equal(aborted, true)
  } finally {
    store.dispose()
    globalThis.fetch = realFetch
  }
})
