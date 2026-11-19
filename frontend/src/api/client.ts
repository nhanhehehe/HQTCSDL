export class ApiError extends Error {
  constructor(public status: number, public requestId: string | null, public code = '', message?: string) {
    super(message ?? `Yêu cầu thất bại (HTTP ${status})`)
  }
}

export type RequestOptions = RequestInit & {
  timeoutMs?: number
}

export type CsvDownloadResult = {
  blob: Blob
  filename: string
  contentType: string
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

function setupController(options: RequestOptions) {
  const started = generation
  const controller = new AbortController()
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) controller.abort()
  const timeoutMs = options.timeoutMs ?? 15000
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  return { started, controller, abort, timeout }
}

function parseContentDispositionFilename(header: string | null): string {
  if (!header) return 'measurements_report.csv'
  const quoted = /filename="([^"]+)"/i.exec(header)
  const raw = quoted?.[1] ?? /filename=([^;]+)/i.exec(header)?.[1]?.trim()
  if (!raw) return 'measurements_report.csv'
  const sanitized = raw.replaceAll('/', '_').replaceAll('\\', '_').trim()
  return sanitized || 'measurements_report.csv'
}

export async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const isWeb = path.startsWith('/api/v1/')
  const { started, controller, abort, timeout } = setupController(options)
  if (isWeb) pending.add(controller)
  const { timeoutMs: _ignored, ...fetchOptions } = options
  try {
    const response = await fetch(path, {
      ...fetchOptions, signal: controller.signal, credentials: 'same-origin', cache: 'no-store',
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

export async function requestCsvBlob(
  path: string,
  options: RequestOptions = {},
): Promise<CsvDownloadResult> {
  const isWeb = path.startsWith('/api/v1/')
  const { started, controller, abort, timeout } = setupController(options)
  if (isWeb) pending.add(controller)
  const { timeoutMs: _ignored, ...fetchOptions } = options
  try {
    const response = await fetch(path, {
      ...fetchOptions, signal: controller.signal, credentials: 'same-origin', cache: 'no-store',
    })
    if (!response.ok) {
      let body: unknown
      try { body = await response.json() } catch { body = undefined }
      if (isWeb && started !== generation) throw new StaleResponse()
      if (isWeb && response.status === 401 && path !== '/api/v1/auth/login') {
        for (const callback of unauthorized) callback()
      }
      const envelope = (body ?? {}) as { error?: { code?: string; message?: string } }
      throw new ApiError(response.status, response.headers.get('X-Request-ID'),
        envelope.error?.code, envelope.error?.message)
    }
    const contentType = response.headers.get('content-type') ?? ''
    if (response.status !== 200 || !contentType.toLowerCase().startsWith('text/csv')) {
      throw new ApiError(
        response.status,
        response.headers.get('X-Request-ID'),
        'CSV_INVALID_RESPONSE',
        'Phản hồi tải CSV không đúng định dạng text/csv.',
      )
    }
    const blob = await response.blob()
    if ((isWeb && started !== generation) || controller.signal.aborted) {
      throw new StaleResponse()
    }
    return {
      blob,
      filename: parseContentDispositionFilename(response.headers.get('Content-Disposition')),
      contentType,
    }
  } finally {
    clearTimeout(timeout)
    pending.delete(controller)
    options.signal?.removeEventListener('abort', abort)
  }
}

export function getJson<T>(path: string, signal?: AbortSignal, timeoutMs?: number): Promise<T> {
  return requestJson<T>(path, { signal, timeoutMs })
}
