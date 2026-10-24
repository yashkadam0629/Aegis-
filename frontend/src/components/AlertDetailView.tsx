import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { AlertDetail } from '../types'
import { dt, f3, inr, label, num } from '../utils/format'
import { EChart, axis } from './EChart'
import { BandBadge, ErrorBox, Notice, Spinner, StatusBadge, Tabs, Tag } from './ui'

export function AlertDetailView({ alertId, onChanged }: { alertId: string; onChanged?: () => void }) {
  const nav = useNavigate()
  const { data, loading, error, reload } = useAsync(() => api.get<AlertDetail>(`/alerts/${alertId}`), [alertId])
  const [tab, setTab] = useState('summary')
  const [priority, setPriority] = useState('')
  const [assignee, setAssignee] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  if (loading && !data) return <Spinner />
  if (error) return <ErrorBox message={error} onRetry={reload} />
  if (!data) return null
  const d = data.detail
  const open = data.investigations.find((i) => i.status !== 'Closed')

  const create = async () => {
    setBusy(true)
    setErr(null)
    try {
      const inv = await api.post<{ investigation_id: string }>('/investigations', { alert_id: alertId, priority: priority || undefined, assigned_analyst: assignee || undefined })
      onChanged?.()
      nav(`/investigations/${inv.investigation_id}`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="space-y-3 border-b border-ink-100 px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <BandBadge band={data.risk_band} />
          <span className="tabular text-base font-semibold">{f3(data.risk_score)}</span>
          <span className="text-ink-500">model-generated risk score (not a probability)</span>
          <StatusBadge status={data.investigation_status} />
        </div>
        <p className="text-ink-800">{d.evidence_summary}</p>
        <div className="flex flex-wrap gap-1.5">{d.detection_methods.map((m) => <Tag key={m}>{label(m)}</Tag>)}</div>
        <Notice>{data.disclaimer}</Notice>
      </div>
      <Tabs tabs={[{ id: 'summary', label: 'Summary' }, { id: 'evidence', label: `Evidence (${data.transactions.length})` }, { id: 'explain', label: 'Explanation' }, { id: 'case', label: 'Investigation' }]} value={tab} onChange={setTab} />
      <div className="p-5">
        {tab === 'summary' && (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-3">
              {[['Entity', data.entity_id], ['Accounts involved', num(data.n_accounts)], ['Transactions', num(data.n_transactions)], ['Gross value', inr(data.gross_value)], ['Estimated exposed value', inr(data.exposed_value)], ['Evidence window', `${dt(d.evidence_timestamps.first)} → ${dt(d.evidence_timestamps.last)}`], ['Model version', data.model_version], ['Feature version', data.feature_version], ['Alert ID', data.alert_id]].map(([a, b]) => (
                <div key={a}><dt className="label">{a}</dt><dd className="break-words">{b}</dd></div>
              ))}
            </dl>
            <Notice>{d.exposure.definition} Method: {d.exposure.method}.</Notice>
            {d.detections.map((x, i) => (
              <div key={i} className="panel p-3">
                <div className="flex items-center justify-between"><strong>{label(x.method)}</strong><span className="text-ink-500">pattern strength {f3(x.strength)}</span></div>
                <p className="mt-1 text-ink-700">{x.summary}</p>
                {x.path.length > 0 && <p className="mt-1 break-words font-mono text-[12px] text-ink-600">{x.path.join(' → ')}</p>}
              </div>
            ))}
            <div>
              <h2 className="mb-1">Recommended investigation steps</h2>
              <ol className="list-decimal space-y-1 pl-5 text-ink-700">{d.recommended_steps.map((s) => <li key={s}>{s}</li>)}</ol>
            </div>
            <div>
              <h2 className="mb-1">Data limitations</h2>
              <ul className="list-disc space-y-1 pl-5 text-ink-600">{d.limitations.map((s) => <li key={s}>{s}</li>)}</ul>
            </div>
            <div className="flex gap-3">
              <Link className="btn" to={`/network?account=${data.entity_id}&alert=${data.alert_id}`}>Open in Network Explorer</Link>
            </div>
          </div>
        )}
        {tab === 'evidence' && (
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]" aria-label="Alert evidence transactions">
              <thead><tr><th className="th">Transaction</th><th className="th">Time (UTC)</th><th className="th">From</th><th className="th">To</th><th className="th text-right">Amount</th><th className="th text-right">Model risk</th></tr></thead>
              <tbody>
                {data.transactions.map((t) => (
                  <tr key={t.transaction_id}>
                    <td className="td font-mono">{t.transaction_id}</td><td className="td tabular whitespace-nowrap">{dt(t.transaction_timestamp)}</td>
                    <td className="td font-mono">{t.source_account_id}</td><td className="td font-mono">{t.destination_account_id}</td>
                    <td className="td tabular text-right">{inr(t.amount)}</td><td className="td tabular text-right">{t.model_risk == null ? '—' : f3(t.model_risk)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {tab === 'explain' && (
          <div className="space-y-4">
            <div>
              <h2 className="mb-1">How the risk score was combined</h2>
              <p className="mb-2 text-ink-600">{d.risk_breakdown.method}. Correlated signals are collapsed within a family before combining, so evidence is not double-counted.</p>
              <table className="w-full text-[13px]"><thead><tr><th className="th">Signal family</th><th className="th text-right">Normalised signal</th><th className="th text-right">Contribution</th></tr></thead>
                <tbody>
                  {Object.entries(d.risk_breakdown.signals).map(([k, v]) => <tr key={k}><td className="td">{label(k)}</td><td className="td tabular text-right">{f3(v)}</td><td className="td tabular text-right">{f3(d.risk_breakdown.contributions[k])}</td></tr>)}
                  {d.risk_breakdown.missing_families.map((k) => <tr key={k}><td className="td text-ink-400">{label(k)}</td><td className="td text-right text-ink-400" colSpan={2}>not available for this alert</td></tr>)}
                </tbody></table>
              <p className="mt-2 text-ink-600">Alternative scores for comparison — rules only: {f3(d.alternative_scores.rules_only)} · model only: {f3(d.alternative_scores.model_only)}</p>
            </div>
            {d.explanation ? (
              <div>
                <h2 className="mb-1">Features driving the highest-scoring transaction ({d.explained_transaction})</h2>
                <EChart
                  ariaLabel="Bar chart of feature contributions to the model score"
                  height={Math.max(160, d.explanation.factors.length * 34)}
                  option={{
                    grid: { left: 150, right: 30, top: 6, bottom: 24 },
                    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
                    xAxis: { type: 'value', name: 'contribution (log-odds)', nameLocation: 'middle', nameGap: 22, ...axis() },
                    yAxis: { type: 'category', inverse: true, data: d.explanation.factors.map((f) => `${f.feature}=${f.value === null ? 'NaN' : +f.value.toPrecision(3)}`), ...axis() },
                    series: [{ type: 'bar', data: d.explanation.factors.map((f) => ({ value: +f.contribution.toFixed(3), itemStyle: { color: f.contribution > 0 ? '#b42318' : '#3f7d58' } })) }],
                  }}
                />
                <p className="text-[12px] text-ink-500">{d.explanation.method}. Red raises, green lowers the score. Attributions describe model behaviour — they are not proof of causality or intent.</p>
              </div>
            ) : <Notice>No feature explanation is stored for this alert.</Notice>}
          </div>
        )}
        {tab === 'case' && (
          <div className="space-y-3">
            {data.investigations.length > 0 && (
              <div>
                <h2 className="mb-1">Linked investigations</h2>
                {data.investigations.map((i) => (
                  <Link key={i.investigation_id} to={`/investigations/${i.investigation_id}`} className="mb-1 flex items-center gap-3 rounded border border-ink-200 px-3 py-2 hover:bg-ink-50">
                    <span className="font-mono">{i.investigation_id}</span><StatusBadge status={i.status} /><span>{i.priority}</span><span className="text-ink-500">{i.assigned_analyst ?? 'unassigned'}</span>
                  </Link>
                ))}
              </div>
            )}
            {!open && (
              <div className="panel space-y-3 p-4">
                <h2>Create investigation from this alert</h2>
                <div className="grid grid-cols-2 gap-3">
                  <label><span className="label">Priority</span>
                    <select className="input w-full" value={priority} onChange={(e) => setPriority(e.target.value)}>
                      <option value="">Default from risk band ({data.risk_band})</option>{['Low', 'Medium', 'High', 'Critical'].map((p) => <option key={p}>{p}</option>)}
                    </select></label>
                  <label><span className="label">Assign analyst</span><input className="input w-full" value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder="optional" /></label>
                </div>
                {err && <div role="alert" className="text-risk-critical">{err}</div>}
                <button className="btn btn-primary" disabled={busy} onClick={create}>{busy ? 'Creating…' : 'Create investigation'}</button>
                <p className="text-[12px] text-ink-500">The alert's transactions are attached as evidence. Every change is recorded in an audit trail.</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
