import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Drawer, Empty, ErrorBox, Field, Pager, Panel, Spinner, BandBadge } from '../components/ui'
import { useDataset } from '../hooks/DatasetContext'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { Page, Txn } from '../types'
import { dt, f3, inr, label } from '../utils/format'

interface Detail extends Txn {
  scores: { model_risk: number; anomaly_percentile: number; rule_score: number } | null
  alerts: { alert_id: string; alert_type: string; risk_band: string; risk_score: number }[]
  ingested_at: string
  status: string
}

export default function Transactions() {
  const { datasetId } = useDataset()
  const [sp, setSp] = useSearchParams()
  const [q, setQ] = useState(sp.get('account') || '')
  const [account, setAccount] = useState(sp.get('account') || '')
  const [direction, setDirection] = useState('any')
  const [minA, setMinA] = useState('')
  const [maxA, setMaxA] = useState('')
  const [type, setType] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [sort, setSort] = useState('transaction_timestamp')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)
  const [sel, setSel] = useState<string | null>(null)
  const size = 50

  useEffect(() => setPage(1), [account, direction, minA, maxA, type, from, to, datasetId])
  const { data, loading, error, reload } = useAsync(
    () =>
      api.get<Page<Txn>>('/transactions', {
        dataset_id: datasetId, q: account && !/^AC/i.test(account) ? account : undefined, account: /^AC/i.test(account) ? account : undefined, direction,
        min_amount: minA, max_amount: maxA, transaction_type: type, date_from: from ? from + 'T00:00:00' : undefined, date_to: to ? to + 'T23:59:59' : undefined, sort, order, page, page_size: size,
      }),
    [datasetId, account, direction, minA, maxA, type, from, to, sort, order, page],
    !!datasetId,
  )
  const detail = useAsync(() => api.get<Detail>(`/transactions/${sel}`, { dataset_id: datasetId }), [sel, datasetId], !!sel)

  const th = (col: string, text: string, right = false) => (
    <th className={`th ${right ? 'text-right' : ''}`} aria-sort={sort === col ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button className="uppercase" onClick={() => { if (sort === col) setOrder(order === 'asc' ? 'desc' : 'asc'); else { setSort(col); setOrder('desc') } }}>
        {text} {sort === col ? (order === 'asc' ? '▲' : '▼') : ''}
      </button>
    </th>
  )

  return (
    <div className="space-y-4">
      <div>
        <h1>Transaction Explorer</h1>
        <p className="text-ink-500">Search by transaction ID or account; risk scores are model-generated and only present for the analysed window.</p>
      </div>
      <Panel>
        <form
          className="grid grid-cols-2 gap-3 p-4 md:grid-cols-4 xl:grid-cols-8"
          onSubmit={(e) => { e.preventDefault(); setAccount(q.trim()); setSp(q.trim() ? { account: q.trim() } : {}) }}
          aria-label="Transaction filters"
        >
          <div className="col-span-2">
            <Field label="Transaction ID or account"><input className="input w-full" value={q} onChange={(e) => setQ(e.target.value)} placeholder="TX00001234 or AC000123" /></Field>
          </div>
          <Field label="Direction">
            <select className="input w-full" value={direction} onChange={(e) => setDirection(e.target.value)} disabled={!/^AC/i.test(account)}>
              <option value="any">Incoming + outgoing</option><option value="in">Incoming only</option><option value="out">Outgoing only</option>
            </select>
          </Field>
          <Field label="Type">
            <select className="input w-full" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">All types</option>
              {['P2P_TRANSFER', 'MERCHANT_PAYMENT', 'BUSINESS_TRANSFER', 'BILL_PAYMENT', 'MERCHANT_SETTLEMENT'].map((t) => <option key={t} value={t}>{label(t)}</option>)}
            </select>
          </Field>
          <Field label="Min amount"><input className="input w-full" type="number" min="0" value={minA} onChange={(e) => setMinA(e.target.value)} /></Field>
          <Field label="Max amount"><input className="input w-full" type="number" min="0" value={maxA} onChange={(e) => setMaxA(e.target.value)} /></Field>
          <Field label="From"><input className="input w-full" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
          <Field label="To"><input className="input w-full" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
          <div className="flex items-end gap-2">
            <button className="btn btn-primary" type="submit">Search</button>
            <button className="btn" type="button" onClick={() => { setQ(''); setAccount(''); setSp({}); setMinA(''); setMaxA(''); setType(''); setFrom(''); setTo(''); setDirection('any') }}>Reset</button>
          </div>
        </form>
      </Panel>
      <Panel>
        {loading && !data ? <Spinner /> : error ? <ErrorBox message={error} onRetry={reload} /> : !data || data.items.length === 0 ? <Empty title="No transactions match" hint="Adjust or reset the filters." /> : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]" aria-label="Transactions">
                <thead><tr>
                  {th('transaction_id', 'Transaction')}{th('transaction_timestamp', 'Time (UTC)')}{th('source_account_id', 'From')}{th('destination_account_id', 'To')}
                  {th('amount', 'Amount', true)}<th className="th">Type</th>{th('model_risk', 'Model risk', true)}
                </tr></thead>
                <tbody>
                  {data.items.map((t) => (
                    <tr key={t.transaction_id} className="cursor-pointer hover:bg-brand-50" onClick={() => setSel(t.transaction_id)}>
                      <td className="td font-mono text-[12px]"><button className="text-brand-600 hover:underline" onClick={(e) => { e.stopPropagation(); setSel(t.transaction_id) }}>{t.transaction_id}</button></td>
                      <td className="td tabular whitespace-nowrap">{dt(t.transaction_timestamp)}</td>
                      <td className="td font-mono text-[12px]"><button className="hover:underline" onClick={(e) => { e.stopPropagation(); setQ(t.source_account_id); setAccount(t.source_account_id) }}>{t.source_account_id}</button></td>
                      <td className="td font-mono text-[12px]"><button className="hover:underline" onClick={(e) => { e.stopPropagation(); setQ(t.destination_account_id); setAccount(t.destination_account_id) }}>{t.destination_account_id}</button></td>
                      <td className="td tabular text-right">{inr(t.amount)}</td>
                      <td className="td">{label(t.transaction_type)}</td>
                      <td className="td tabular text-right">{t.model_risk === null || t.model_risk === undefined ? <span className="text-ink-400">not scored</span> : f3(t.model_risk)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={page} pageSize={size} total={data.total} onPage={setPage} />
          </>
        )}
      </Panel>
      <Drawer open={!!sel} onClose={() => setSel(null)} title={<div><div className="text-[11px] uppercase text-ink-500">Transaction</div><div className="font-mono text-sm font-semibold">{sel}</div></div>}>
        {detail.loading ? <Spinner /> : detail.error ? <ErrorBox message={detail.error} /> : detail.data && (
          <div className="space-y-4 p-5">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
              {[['Amount', inr(detail.data.amount)], ['Timestamp', dt(detail.data.transaction_timestamp)], ['Type', label(detail.data.transaction_type)], ['Channel', detail.data.channel || '—'], ['Status', detail.data.status], ['Ingested', dt(detail.data.ingested_at)]].map(([a, b]) => (
                <div key={a}><dt className="label">{a}</dt><dd>{b}</dd></div>
              ))}
            </dl>
            <div className="grid grid-cols-2 gap-3">
              {[['Source account', detail.data.source_account_id], ['Destination account', detail.data.destination_account_id]].map(([a, id]) => (
                <div key={a} className="panel p-3">
                  <div className="label">{a}</div>
                  <div className="font-mono">{id}</div>
                  <div className="mt-2 flex gap-3">
                    <Link className="text-brand-600 hover:underline" to={`/network?account=${id}`}>View network</Link>
                    <button className="text-brand-600 hover:underline" onClick={() => { setQ(id); setAccount(id); setSel(null) }}>Account transactions</button>
                  </div>
                </div>
              ))}
            </div>
            <div>
              <h2 className="mb-1">Scores</h2>
              {detail.data.scores ? (
                <p className="text-ink-700">Model risk {f3(detail.data.scores.model_risk)} · anomaly percentile {f3(detail.data.scores.anomaly_percentile)} · rule score {f3(detail.data.scores.rule_score)}<br /><span className="text-ink-500">Model-generated risk scores, not probabilities.</span></p>
              ) : <p className="text-ink-500">Not scored (outside the analysed window).</p>}
            </div>
            <div>
              <h2 className="mb-1">Linked alerts</h2>
              {detail.data.alerts.length === 0 ? <p className="text-ink-500">None.</p> : detail.data.alerts.map((a) => (
                <Link key={a.alert_id} to={`/alerts?open=${a.alert_id}`} className="mr-2 inline-flex items-center gap-2 rounded border border-ink-200 px-2 py-1 hover:bg-ink-50">
                  <span className="font-mono text-[12px]">{a.alert_id}</span><BandBadge band={a.risk_band} /><span>{label(a.alert_type)}</span>
                </Link>
              ))}
            </div>
          </div>
        )}
      </Drawer>
    </div>
  )
}
