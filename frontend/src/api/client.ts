export class ApiError extends Error {
  constructor(public status: number, public requestId: string | null, public code = '', message?: string) {
    super(message ?? `Yêu cầu thất bại (HTTP ${status})`)
  }
}

let generation = 0
const pending = new Set<AbortController>()
const unauthorized = new Set<() => void>()

export class StaleResponse extends Error {}

export function invalidateRequests() {
  generation += 1
  for (const controller of pending) controller.abort()
  pending.clear()
}

export function onUnauthorized(callback: () => void) {
  unauthorized.add(callback)
  return () => { unauthorized.delete(callback) }
}

export async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const isWeb = path.startsWith('/api/v1/')
  const started = generation
  const controller = new AbortController()
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) controller.abort()
  if (isWeb) pending.add(controller)
  const timeout = setTimeout(() => controller.abort(), 15000)
  try {
    const response = await fetch(path, {
      ...options, signal: controller.signal, credentials: 'same-origin', cache: 'no-store',
    })
    let body: unknown
    if (response.status !== 204) {
      try { body = await response.json() }
      catch (error) { if (response.ok) throw error }
    }
    if (isWeb && started !== generation) throw new StaleResponse()
    if (!response.ok) {
      if (isWeb && response.status === 401 && path !== '/api/v1/auth/login') {
        for (const callback of unauthorized) callback()
      }
      const envelope = (body ?? {}) as { error?: { code?: string; message?: string } }
      throw new ApiError(response.status, response.headers.get('X-Request-ID'),
        envelope.error?.code, envelope.error?.message)
    }
    return body as T
  } finally {
    clearTimeout(timeout)
    pending.delete(controller)
    options.signal?.removeEventListener('abort', abort)
  }
}

export function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return requestJson<T>(path, { signal })
}
