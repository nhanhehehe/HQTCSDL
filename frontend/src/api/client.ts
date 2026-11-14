export class ApiError extends Error {
  constructor(public status: number, public requestId: string | null) {
    super(`Yêu cầu thất bại (HTTP ${status})`)
  }
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, credentials: 'same-origin' })
  if (!response.ok) throw new ApiError(response.status, response.headers.get('X-Request-ID'))
  return response.json() as Promise<T>
}
// Business DTOs and session behavior are added from reviewed flow contracts.
