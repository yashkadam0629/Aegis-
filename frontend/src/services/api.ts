// Thin typed client for the AEGIS REST API. Analyst identity is a placeholder header (no real auth in this prototype).
const BASE = '/api'
const API_KEY = (import.meta as any).env?.VITE_API_KEY as string | undefined

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export function analyst(): string {
  try {
    return localStorage.getItem('aegis.analyst') || 'analyst@local'
  } catch {
    return 'analyst@local'
  }
}

type Params = Record<string, string | number | boolean | undefined | null>

function qs(params?: Params): string {
  if (!params) return ''
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') u.set(k, String(v))
  const s = u.toString()
  return s ? `?${s}` : ''
}

async function request<T>(method: string, path: string, opts: { params?: Params; body?: unknown; form?: FormData } = {}): Promise<T> {
  const headers: Record<string, string> = { 'X-Analyst': analyst() }
  if (API_KEY) headers['X-API-Key'] = API_KEY
  let body: BodyInit | undefined
  if (opts.form) body = opts.form
  else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  const res = await fetch(`${BASE}${path}${qs(opts.params)}`, { method, headers, body })
  if (!res.ok) {
    let msg = res.statusText
    try {
      const j = await res.json()
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg)
  }
  return (await res.json()) as T
}

export const api = {
  get: <T>(path: string, params?: Params) => request<T>('GET', path, { params }),
  post: <T>(path: string, body?: unknown, params?: Params) => request<T>('POST', path, { body, params }),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, { body }),
  del: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, form: FormData) => request<T>('POST', path, { form }),
}
