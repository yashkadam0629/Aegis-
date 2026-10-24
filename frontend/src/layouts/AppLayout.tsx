import { NavLink, Outlet } from 'react-router-dom'
import { useState } from 'react'
import { useDataset } from '../hooks/DatasetContext'
import { useAsync } from '../hooks/useAsync'
import { api, analyst } from '../services/api'
import type { Overview } from '../types'
import { d0, dt } from '../utils/format'

const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/transactions', label: 'Transactions' },
  { to: '/network', label: 'Network Explorer' },
  { to: '/alerts', label: 'Alert Center' },
  { to: '/intelligence', label: 'Network Intelligence' },
  { to: '/investigations', label: 'Investigations' },
  { to: '/models', label: 'Model Performance' },
  { to: '/insights', label: 'Executive Insights' },
  { to: '/data', label: 'Data Management' },
]

export function Logo() {
  return (
    <div className="flex items-center gap-2.5 px-4 py-4">
      <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
        <path d="M16 3l11 4.2v8.3c0 6.6-4.6 11.3-11 13.8C9.6 26.800 5 22.100 5 15.500V7.200z" fill="none" stroke="#6ea0ff" strokeWidth="2" />
        <circle cx="16" cy="13.500" r="2.400" fill="#6ea0ff" />
        <path d="M16 16v6M11.500 11.500L8.500 14M20.500 11.500l3 2.500" stroke="#6ea0ff" strokeWidth="1.700" strokeLinecap="round" />
      </svg>
      <div>
        <div className="text-[15px] font-semibold leading-none tracking-wide text-white">AEGIS</div>
        <div className="mt-1 text-[10px] leading-none text-ink-400">Financial crime intelligence</div>
      </div>
    </div>
  )
}

export default function AppLayout() {
  const { datasets, dataset, setDatasetId, error } = useDataset()
  const ov = useAsync(() => api.get<Overview>('/overview', { dataset_id: dataset?.dataset_id }), [dataset?.dataset_id], !!dataset)
  const [name, setName] = useState(analyst())
  const [nav, setNav] = useState(false)
  const model = ov.data?.model
  const fresh = ov.data?.freshness

  return (
    <div className="flex h-full">
      <aside className={`${nav ? 'flex' : 'hidden'} fixed inset-y-0 left-0 z-30 w-56 shrink-0 flex-col bg-ink-900 md:static md:flex`}>
        <Logo />
        <nav className="flex-1 space-y-0.5 px-2" aria-label="Primary">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              onClick={() => setNav(false)}
              className={({ isActive }) => `block rounded px-3 py-2 text-[13px] ${isActive ? 'bg-ink-700 font-medium text-white' : 'text-ink-300 hover:bg-ink-800 hover:text-white'}`}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-ink-800 p-3 text-[10.500px] leading-snug text-ink-400">
          Research &amp; investigation-support prototype. Scores are model-generated leads, not findings of wrongdoing.
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-ink-200 bg-white px-4 py-2.5">
          <button className="btn md:hidden" onClick={() => setNav(!nav)} aria-label="Toggle navigation">
            Menu
          </button>
          <div className="flex items-center gap-2">
            <span className="label !mb-0">Dataset</span>
            <select className="input !py-1" aria-label="Dataset" value={dataset?.dataset_id ?? ''} onChange={(e) => setDatasetId(e.target.value)}>
              {datasets.length === 0 && <option value="">No datasets</option>}
              {datasets.map((d) => (
                <option key={d.dataset_id} value={d.dataset_id}>
                  {d.name}
                </option>
              ))}
            </select>
            {dataset && (
              <span className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-900" title={dataset.provenance}>
                {dataset.kind === 'synthetic' ? 'SYNTHETIC DATA' : 'Uploaded data'}
              </span>
            )}
          </div>
          {dataset && (
            <div className="tabular text-ink-600">
              {d0(dataset.min_ts)} → {d0(dataset.max_ts)}
            </div>
          )}
          <div className="ml-auto flex flex-wrap items-center gap-x-5 gap-y-1 text-ink-600">
            <span title="Model used to score the latest pipeline run">
              Model: <span className="font-medium text-ink-800">{model ? model.version : 'none'}</span>
            </span>
            <span title="Time of the latest completed analysis run">
              Data freshness: <span className="font-medium text-ink-800">{fresh?.last_pipeline_run ? dt(fresh.last_pipeline_run) : 'not analysed'}</span>
            </span>
            <label className="flex items-center gap-1.5">
              <span className="sr-only">Analyst identity</span>
              <span className="label !mb-0">Analyst</span>
              <input
                className="input !w-40 !py-1"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onBlur={() => {
                  try {
                    localStorage.setItem('aegis.analyst', name || 'analyst@local')
                  } catch {
                    /* ignore */
                  }
                }}
                aria-label="Analyst identity"
              />
            </label>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
          {error && (
            <div role="alert" className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-risk-critical">
              Cannot reach the AEGIS API ({error}). Start the backend and refresh.
            </div>
          )}
          <Outlet />
        </main>
      </div>
    </div>
  )
}
