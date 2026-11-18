import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const clientSource = await readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const compiledClient = ts.transpileModule(clientSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText

async function loadClient() {
  return import(
    `data:text/javascript;base64,${Buffer.from(compiledClient + `\n//${Math.random()}`).toString('base64')}`
  )
}

test('G1 area archive 409 AREA_IN_USE and gateway credential issue/revoke contract', async () => {
  const { requestJson } = await loadClient()
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
  const { requestJson, invalidateRequests, onUnauthorized, StaleResponse } = await loadClient()
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
