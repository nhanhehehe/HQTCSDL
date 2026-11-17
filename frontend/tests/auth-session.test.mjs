import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { setImmediate as immediate } from 'node:timers/promises'
import test from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText
  return `data:text/javascript;base64,${Buffer.from(compiled + `\n// ${Math.random()}`).toString('base64')}`
}
const clientSource = await readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const sessionSource = await readFile(new URL('../src/features/auth/session.ts', import.meta.url), 'utf8')
const json = (value, status = 200) => new Response(JSON.stringify(value), { status })
const valid = (patch = {}) => ({ admin: { id: '1', username: 'test-admin' },
  expires_at: new Date(Date.now() + 60_000).toISOString(), csrf_token: 'test-session-a', ...patch })
function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

async function fixture(t, initial = valid()) {
  const url = moduleUrl(clientSource)
  const api = await import(url)
  const { AuthSession } = await import(moduleUrl(sessionSource.replace("'../../api/client'", JSON.stringify(url))))
  const original = globalThis.fetch
  const requests = []
  let handler = async () => json(initial)
  globalThis.fetch = (path, options) => {
    requests.push({ path, options })
    return handler(path, options)
  }
  const auth = new AuthSession()
  t.after(() => { auth.stop(); globalThis.fetch = original })
  auth.start()
  await immediate()
  assert.equal(auth.getSnapshot().busy, false)
  assert.equal(auth.canWrite(), true)
  return { auth, api, requests, respond: value => { handler = value } }
}

test('same-session revalidation retains session identity and does not cancel profile requests', async t => {
  const { auth, api, requests, respond } = await fixture(t)
  const original = auth.getSnapshot().session
  const states = []
  auth.subscribe(() => states.push(auth.getSnapshot()))
  const check = deferred()
  const profile = deferred()
  respond(path => path === '/api/v1/auth/session' ? check.promise : profile.promise)
  const reading = api.getJson('/api/v1/nodes/1')
  const verifying = auth.verify()
  assert.equal(auth.canWrite(), false)
  assert.equal(auth.getSnapshot().session, original)
  await auth.verify() // overlapping manual verifications are serialized
  assert.equal(requests.filter(r => r.path === '/api/v1/auth/session').length, 2)
  check.resolve(json(original))
  await verifying
  assert.equal(auth.canWrite(), true)
  assert.ok(states.every(s => s.session?.csrf_token === original.csrf_token))
  profile.resolve(json({ id: '1', row_version: 'AAAAAAAAAAE=' }))
  assert.equal((await reading).row_version, 'AAAAAAAAAAE=')
})

for (const failure of ['network', '503']) {
  test(`${failure} keeps the draft's session but locks writes until explicit revalidation succeeds`, async t => {
    const { auth, requests, respond } = await fixture(t)
    const original = auth.getSnapshot().session
    respond(async () => {
      if (failure === 'network') throw new TypeError('offline')
      return json({ error: { code: 'STORAGE_UNAVAILABLE' } }, 503)
    })
    await auth.verify()
    assert.equal(auth.getSnapshot().session, original)
    assert.equal(auth.getSnapshot().busy, false)
    assert.equal(auth.canWrite(), false)
    assert.match(auth.getSnapshot().message, /kiểm tra phiên lại/)
    assert.equal(requests.length, 2) // never automatically retries
    respond(async () => json(original))
    await auth.verify()
    assert.equal(auth.canWrite(), true)
  })
}

test('401 clears private state and an old successful profile response is rejected', async t => {
  const { auth, api, respond } = await fixture(t)
  const profile = deferred()
  respond(path => path === '/api/v1/auth/session'
    ? Promise.resolve(json({ error: { code: 'SESSION_INVALID' } }, 401)) : profile.promise)
  const pending = api.getJson('/api/v1/nodes/1')
  await auth.verify()
  assert.equal(auth.getSnapshot().session, null)
  assert.equal(auth.canWrite(), false)
  profile.resolve(json({ id: '1' }))
  await assert.rejects(pending, api.StaleResponse)
})

test('a protected request returning 401 prevents an older verification from restoring the session', async t => {
  const { auth, api, respond } = await fixture(t)
  const original = auth.getSnapshot().session
  const check = deferred()
  respond(path => path === '/api/v1/auth/session' ? check.promise
    : Promise.resolve(json({ error: { code: 'SESSION_INVALID' } }, 401)))
  const pending = auth.verify()
  await assert.rejects(api.getJson('/api/v1/nodes'), api.ApiError)
  check.resolve(json(original))
  await pending
  assert.equal(auth.getSnapshot().session, null)
  assert.equal(auth.canWrite(), false)
})

test('expiry clears the draft session even while a verification is pending', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1_800_000_000_000 })
  const { auth, respond } = await fixture(t, valid({ expires_at: new Date(Date.now() + 1000).toISOString() }))
  const original = auth.getSnapshot().session
  const check = deferred()
  respond(() => check.promise)
  const pending = auth.verify()
  t.mock.timers.tick(1000)
  assert.equal(auth.getSnapshot().session, null)
  assert.equal(auth.canWrite(), false)
  check.resolve(json(original))
  await pending
  assert.equal(auth.getSnapshot().session, null)
})

test('a new session replaces the form identity and invalidates responses from the previous session', async t => {
  const { auth, api, respond } = await fixture(t)
  const profile = deferred()
  const replacement = valid({ admin: { id: '2', username: 'other-admin' }, csrf_token: 'test-session-b' })
  respond(path => path === '/api/v1/auth/session' ? Promise.resolve(json(replacement)) : profile.promise)
  const reading = api.getJson('/api/v1/nodes/1')
  await auth.verify()
  assert.equal(auth.getSnapshot().session.csrf_token, replacement.csrf_token)
  assert.equal(auth.canWrite(), true)
  profile.resolve(json({ id: '1' }))
  await assert.rejects(reading, api.StaleResponse)
})

test('unknown logout immediately hides private state and cannot be undone by manual verification or login', async t => {
  const { auth, requests, respond } = await fixture(t)
  const check = deferred()
  respond(() => check.promise)
  const logout = auth.logout()
  assert.equal(auth.getSnapshot().session, null)
  assert.equal(auth.getSnapshot().uncertainLogout, true)
  check.resolve(Promise.reject(new TypeError('connection lost')))
  await logout
  const count = requests.length
  await auth.verify()
  await auth.login('test-admin', 'fixture-password')
  assert.equal(requests.length, count)
  assert.equal(auth.canWrite(), false)
  respond(async path => path === '/api/v1/auth/session' ? json(valid()) : new Response(null, { status: 204 }))
  await auth.logout()
  assert.equal(auth.getSnapshot().uncertainLogout, false)
  assert.equal(auth.getSnapshot().session, null)
  assert.equal(auth.canWrite(), false)
})

test('StrictMode-style stop/start drops old verification and releases the operation lock', async t => {
  const { auth, respond } = await fixture(t)
  const old = deferred()
  respond(() => old.promise)
  const pending = auth.verify()
  auth.stop()
  const replacement = valid({ csrf_token: 'test-session-c' })
  respond(async () => json(replacement))
  auth.start()
  await immediate()
  assert.equal(auth.getSnapshot().session.csrf_token, replacement.csrf_token)
  old.resolve(json(valid({ csrf_token: 'old-session' })))
  await pending
  assert.equal(auth.getSnapshot().session.csrf_token, replacement.csrf_token)
  assert.equal(auth.canWrite(), true)
})

test('login remains serialized, sends the existing guards, and invalid credentials cannot create a session', async t => {
  const { auth, requests, respond } = await fixture(t)
  const result = deferred()
  respond(() => result.promise)
  const login = auth.login('test-admin', 'fixture-password')
  assert.equal(auth.getSnapshot().session, null)
  await auth.login('test-admin', 'fixture-password')
  assert.equal(requests.length, 2)
  assert.equal(requests[1].options.headers['X-HQ-Client'], 'web')
  assert.equal(requests[1].options.headers['Content-Type'], 'application/json')
  result.resolve(json({ error: { code: 'INVALID_CREDENTIALS', message: 'Wrong credentials' } }, 401))
  await login
  assert.equal(auth.getSnapshot().session, null)
  assert.equal(auth.getSnapshot().message, 'Wrong credentials')
  assert.equal(auth.getSnapshot().busy, false)
})

test('logout retry confirms success when the session is already invalid', async t => {
  const { auth, respond } = await fixture(t)
  respond(async () => { throw new TypeError('lost logout response') })
  await auth.logout()
  assert.equal(auth.getSnapshot().uncertainLogout, true)
  respond(async path => path === '/api/v1/auth/session'
    ? json({ error: { code: 'SESSION_INVALID' } }, 401) : new Response(null, { status: 204 }))
  await auth.logout()
  assert.equal(auth.getSnapshot().uncertainLogout, false)
  assert.equal(auth.getSnapshot().session, null)
  assert.equal(auth.getSnapshot().message, 'Đã đăng xuất.')
})

test('successful login and logout keep the existing JSON/CSRF guards and private state policy', async t => {
  const { auth, requests, respond } = await fixture(t)
  const next = valid({ csrf_token: 'login-session' })
  respond(async path => path === '/api/v1/auth/login' ? json(next) : new Response(null, { status: 204 }))
  await auth.login('test-admin', 'fixture-password')
  assert.equal(auth.canWrite(), true)
  assert.equal(auth.getSnapshot().session.csrf_token, next.csrf_token)
  await auth.logout()
  assert.equal(requests.at(-1).options.headers['X-CSRF-Token'], next.csrf_token)
  assert.equal(auth.getSnapshot().session, null)
  assert.equal(auth.getSnapshot().uncertainLogout, false)
})
