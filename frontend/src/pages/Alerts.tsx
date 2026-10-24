import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertDetailView } from '../components/AlertDetailView'
import { BandBadge, Drawer, Empty, ErrorBox, Field, Pager, Panel, Spinner, StatusBadge } from '../components/ui'
import { useDataset } from '../hooks/DatasetContext'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { AlertRow, Page } from '../types'
import { dt, f3, inr, label } from '../utils/format'

type Resp = Page<AlertRow> & { facets: { band: Record<string, number>; alert_type: Record<string, number> } }

export default function Alerts() {
  const { datasetId } = useDataset()
  const [sp, setSp] = useSearchParams()
  const [band, setBand] = useState('')
  const [type, setType] = useState('')
  const [status, setStatus] = useState('')
  const [entity, setEntity] = useState('')
  const [sort, setSort] = useState('risk_score')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)
  const open = sp.get('open')
  const size = 25

  useEffect(() => setPage(1), [band, type, status, entity, datasetId])
  const { data, loading, error, reload } = useAsync(
    () => api.get<Resp>('/alerts', { dataset_id: datasetId, band, alert_type: type, status, entity, sort, order, page, page_size: size }),
    [datasetId, band, type, status, entity, sort, order, page], !!datasetId)

  const th = (col: string | null, text: string, right = false) => (
    <th className={`th ${right ? 'text-right' : ''}`} aria-sort={col && sort === col ? (order === 'asc' ? 'ascending' : 'descending') : undefined}>
      {col ? <button className="uppercase" onClick={() => { if (sort === col) setOrder(order === 'asc' ? 'desc' : 'asc'); else { setSort(col); setOrder('desc') } }}>{text} {sort === col ? (order === 'asc' ? '▲' : '▼') : ''}</button> : text}
    </th>)

  return (
    <div className="space-y-4">
      <div>
        <h1>Alert Center</h1>
        <p className="text-ink-500">Potentially suspicious activity prioritised by model-generated risk score. Alerts are leads for analyst review, not findings.</p>
      </div>
      <Panel>
        <div className="grid grid-cols-2 gap-3 p-4 md:grid-cols-5">
          <Field label="Risk band"><select className="input w-full" value={band} onChange={(e) => setBand(e.target.value)}><option value="">All</option>{['Critical', 'High', 'Medium', 'Low'].map((b) => <option key={b} value={b}>{b} ({data?.facets.band[b] ?? 0})</option>)}</select></Field>
          <Field label="Alert type"><select className="input w-full" value={type} onChange={(e) => setType(e.target.value)}><option value="">All</option>{Object.entries(data?.facets.alert_type ?? {}).map(([t, c]) => <option key={t} value={t}>{label(t)} ({c})</option>)}</select></Field>
          <Field label="Review status"><select className="input w-full" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option>{['Not opened', 'New', 'In Review', 'Escalated', 'Resolved', 'Closed'].map((s) => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Entity (account ID)"><input className="input w-full" value={entity} onChange={(e) => setEntity(e.target.value)} placeholder="AC000123" /></Field>
          <div className="flex items-end"><button className="btn" onClick={() => { setBand(''); setType(''); setStatus(''); setEntity('') }}>Reset filters</button></div>
        </div>
      </Panel>
      <Panel>
        {loading && !data ? <Spinner /> : error ? <ErrorBox message={error} onRetry={reload} /> : !data || data.items.length === 0 ? <Empty title="No alerts match" hint="Clear the filters, or run the analysis pipeline from Data Management." /> : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]" aria-label="Alerts">
                <thead><tr>{th(null, 'Alert')}{th(null, 'Entity')}{th('risk_score', 'Risk', true)}{th(null, 'Band')}{th(null, 'Type')}{th('n_transactions', 'Txns', true)}{th('exposed_value', 'Potential exposure', true)}{th('alert_timestamp', 'Detected')}{th(null, 'Review')}{th(null, 'Analyst')}</tr></thead>
                <tbody>
                  {data.items.map((a) => (
                    <tr key={a.alert_id} className="cursor-pointer hover:bg-brand-50" onClick={() => setSp({ open: a.alert_id })}>
                      <td className="td font-mono text-[12px]"><button className="text-brand-600 hover:underline" onClick={(e) => { e.stopPropagation(); setSp({ open: a.alert_id }) }}>{a.alert_id}</button></td>
                      <td className="td font-mono text-[12px]">{a.entity_id}</td>
                      <td className="td tabular text-right font-medium">{f3(a.risk_score)}</td>
                      <td className="td"><BandBadge band={a.risk_band} /></td>
                      <td className="td">{label(a.alert_type)}</td>
                      <td className="td tabular text-right">{a.n_transactions}</td>
                      <td className="td tabular text-right">{inr(a.exposed_value, true)}</td>
                      <td className="td whitespace-nowrap tabular">{dt(a.alert_timestamp)}</td>
                      <td className="td"><StatusBadge status={a.investigation_status} /></td>
                      <td className="td">{a.assigned_analyst ?? <span className="text-ink-400">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={page} pageSize={size} total={data.total} onPage={setPage} />
          </>
        )}
      </Panel>
      <Drawer open={!!open} width={760} onClose={() => setSp({})} title={<div><div className="text-[11px] uppercase text-ink-500">Alert</div><div className="font-mono text-sm font-semibold">{open}</div></div>}>
        {open && <AlertDetailView alertId={open} onChanged={reload} />}
      </Drawer>
    </div>
  )
}
