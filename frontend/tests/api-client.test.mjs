import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText

async function client() {
  return import(`data:text/javascript;base64,${Buffer.from(compiled + `\n//${Math.random()}`).toString('base64')}`)
}

test('old successful response cannot restore data after invalidation', async () => {
  const api = await client()
  let respond
  const original = globalThis.fetch
  globalThis.fetch = () => new Promise(resolve => { respond = resolve })
  try {
    const pending = api.getJson('/api/v1/private')
    api.invalidateRequests()
    respond(new Response(JSON.stringify({ private: true }), { status: 200 }))
    await assert.rejects(pending, api.StaleResponse)
  } finally { globalThis.fetch = original }
})

test('204 logout has no JSON and browser credentials remain same-origin', async () => {
  const api = await client()
  const original = globalThis.fetch
  globalThis.fetch = async (_path, options) => {
    assert.equal(options.credentials, 'same-origin')
    assert.equal(options.cache, 'no-store')
    return new Response(null, { status: 204 })
  }
  try { assert.equal(await api.requestJson('/api/v1/auth/logout', { method: 'POST' }), undefined) }
  finally { globalThis.fetch = original }
})

test('401 clears auth except wrong login credentials and retains error contract', async () => {
  const api = await client()
  const original = globalThis.fetch
  let invalidations = 0
  const unsubscribe = api.onUnauthorized(() => { invalidations += 1 })
  globalThis.fetch = async () => new Response(JSON.stringify({
    error: { code: 'SESSION_INVALID', message: 'Expired' },
  }), { status: 401, headers: { 'X-Request-ID': 'request-1' } })
  try {
    await assert.rejects(api.getJson('/api/v1/auth/session'), error =>
      error instanceof api.ApiError && error.code === 'SESSION_INVALID' && error.requestId === 'request-1')
    assert.equal(invalidations, 1)
    await assert.rejects(api.requestJson('/api/v1/auth/login', { method: 'POST' }), api.ApiError)
    assert.equal(invalidations, 1)
  } finally { unsubscribe(); globalThis.fetch = original }
})

test('mutation network failure is surfaced once without automatic retry', async () => {
  const api = await client()
  const original = globalThis.fetch
  let calls = 0
  globalThis.fetch = async () => { calls += 1; throw new TypeError('network') }
  try {
    await assert.rejects(api.requestJson('/api/v1/auth/logout', { method: 'POST' }), TypeError)
    assert.equal(calls, 1)
  } finally { globalThis.fetch = original }
})
