import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ReactElement } from 'react'
import { DatasetProvider } from '../hooks/DatasetContext'

export interface Call { method: string; url: string; body?: unknown }
type Handler = (url: URL, init: RequestInit) => unknown

/** Installs a fetch mock. Routes are matched by "METHOD /path" prefix; every call is recorded. */
export function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: Call[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), 'http://localhost')
    const method = (init.method || 'GET').toUpperCase()
    const path = url.pathname.replace(/^\/api/, '')
    calls.push({ method, url: url.pathname + url.search, body: init.body ? JSON.parse(String(init.body)) : undefined })
    const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => `${method} ${path}`.startsWith(k))
    if (!key) return new Response(JSON.stringify({ detail: `unmocked ${method} ${path}` }), { status: 404 })
    const h = routes[key]
    const body = typeof h === 'function' ? (h as Handler)(url, init) : h
    if (body instanceof Response) return body
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
  return calls
}

export const DATASET = { dataset_id: 'DS-test', name: 'Test dataset', kind: 'synthetic', dataset_version: 'abc', n_accounts: 10, n_transactions: 100, min_ts: '2026-01-01T00:00:00', max_ts: '2026-02-01T00:00:00', has_ground_truth: true, provenance: 'SYNTHETIC', created_at: '2026-01-01T00:00:00' }

export function renderAt(ui: ReactElement, path = '/', routePath = '*') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <DatasetProvider>
        <Routes>
          <Route path={routePath} element={ui} />
          <Route path="*" element={<div>other page</div>} />
        </Routes>
      </DatasetProvider>
    </MemoryRouter>,
  )
}
