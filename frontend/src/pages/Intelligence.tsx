import { useState } from 'react'
import { Link } from 'react-router-dom'
import { BandBadge, Empty, ErrorBox, Notice, Panel, Spinner, Tabs, Tag } from '../components/ui'
import { useDataset } from '../hooks/DatasetContext'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { AlertRow, Page } from '../types'
import { dt, f3, inr, label, num, pct } from '../utils/format'

interface Community {
  community_id: string; n_accounts: number; n_transactions: number; total_value: number; internal_share: number; internal_value: number; external_out_value: number; external_in_value: number
  density: number; first_activity: string; last_activity: string; dominant_types: Record<string, number>; max_member_risk: number
  top_risk_accounts: { account_id: string; risk: number; alert_types: string[] }[]; shared_counterparties: { account_id: string; n_txn_from_community: number }[]
}
interface CommResp { items: Community[]; total: number; n_communities_all: number; graph: { nodes: number; edges: number }; compute_seconds: number; note: string; window: string[] }

const PATTERN_TABS = [
  { id: 'communities', label: 'Communities' }, { id: 'layered_chain', label: 'Transaction chains' }, { id: 'cycle', label: 'Circular flows' },
  { id: 'shared_beneficiary', label: 'Shared beneficiaries' }, { id: 'fan_out', label: 'Fan-out' }, { id: 'coordinated_group', label: 'Coordinated groups' },
  { id: 'burst', label: 'Bursts & reactivation' },
]

export default function Intelligence() {
  const { datasetId } = useDataset()
  const [tab, setTab] = useState('communities')
  const [minRisk, setMinRisk] = useState(0)
  const comm = useAsync(() => api.get<CommResp>('/graph/communities', { dataset_id: datasetId, min_size: 4, limit: 40, min_risk: minRisk }), [datasetId, minRisk], !!datasetId && tab === 'communities')
  const types = tab === 'burst' ? ['burst', 'dormant_reactivation', 'structuring_like'] : [tab]
  const al = useAsync(async () => {
    const res = await Promise.all(types.map((t) => api.get<Page<AlertRow>>('/alerts', { dataset_id: datasetId, alert_type: t, page_size: 50 })))
    return res.flatMap((r) => r.items).sort((a, b) => b.risk_score - a.risk_score)
  }, [datasetId, tab], !!datasetId && tab !== 'communities')

  return (
    <div className="space-y-4">
      <div>
        <h1>Network Intelligence</h1>
        <p className="text-ink-500">Structural patterns found by the graph engine in the analysed window. Each is an investigative lead; legitimate activity can look the same.</p>
      </div>
      <Panel>
        <Tabs tabs={PATTERN_TABS} value={tab} onChange={setTab} />
        {tab === 'communities' ? (
          <div className="space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2"><span className="label !mb-0">Min member risk</span>
                <select className="input !py-1" value={minRisk} onChange={(e) => setMinRisk(+e.target.value)} aria-label="Minimum member risk">
                  <option value={0}>Any</option><option value={0.2}>≥ 0.2</option><option value={0.4}>≥ 0.4</option><option value={0.6}>≥ 0.6</option>
                </select></label>
              {comm.data && <span className="text-ink-500">{comm.data.total} shown of {comm.data.n_communities_all} communities · graph {num(comm.data.graph.nodes)} accounts / {num(comm.data.graph.edges)} links · computed in {comm.data.compute_seconds}s (cached)</span>}
            </div>
            {comm.data && <Notice>{comm.data.note}</Notice>}
            {comm.loading && !comm.data ? <Spinner label="Detecting communities…" /> : comm.error ? <ErrorBox message={comm.error} onRetry={comm.reload} /> : !comm.data || comm.data.items.length === 0 ? <Empty title="No communities match" /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]" aria-label="Communities">
                  <thead><tr><th className="th">Community</th><th className="th text-right">Accounts</th><th className="th text-right">Txns</th><th className="th text-right">Value</th><th className="th text-right">Internal share</th><th className="th">Activity</th><th className="th">Dominant types</th><th className="th">Highest-risk members</th><th className="th">Shared counterparties</th></tr></thead>
                  <tbody>
                    {comm.data.items.map((c) => (
                      <tr key={c.community_id}>
                        <td className="td font-medium">{c.community_id}<div className="text-[11px] text-ink-500">max risk {f3(c.max_member_risk)}</div></td>
                        <td className="td tabular text-right">{c.n_accounts}</td><td className="td tabular text-right">{num(c.n_transactions)}</td>
                        <td className="td tabular text-right">{inr(c.total_value, true)}<div className="text-[11px] text-ink-500">out {inr(c.external_out_value, true)} · in {inr(c.external_in_value, true)}</div></td>
                        <td className="td tabular text-right">{pct(c.internal_share)}<div className="text-[11px] text-ink-500">density {f3(c.density)}</div></td>
                        <td className="td whitespace-nowrap text-[12px]">{c.first_activity?.slice(0, 10)} → {c.last_activity?.slice(0, 10)}</td>
                        <td className="td">{Object.entries(c.dominant_types).map(([t, n]) => <div key={t} className="text-[12px]">{label(t)} ({n})</div>)}</td>
                        <td className="td">{c.top_risk_accounts.slice(0, 3).map((a) => <div key={a.account_id}><Link className="font-mono text-[12px] text-brand-600 hover:underline" to={`/network?account=${a.account_id}`}>{a.account_id}</Link> <span className="text-ink-500">{f3(a.risk)}</span></div>)}</td>
                        <td className="td">{c.shared_counterparties.length === 0 ? <span className="text-ink-400">—</span> : c.shared_counterparties.slice(0, 3).map((a) => <div key={a.account_id} className="font-mono text-[12px]">{a.account_id} <span className="text-ink-500">×{a.n_txn_from_community}</span></div>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <div className="p-4">
            {al.loading && !al.data ? <Spinner /> : al.error ? <ErrorBox message={al.error} onRetry={al.reload} /> : !al.data || al.data.length === 0 ? <Empty title="No alerts of this pattern" hint="The detector found nothing in the analysed window, or the pipeline has not been run." /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]" aria-label="Pattern alerts">
                  <thead><tr><th className="th">Alert</th><th className="th">Pattern</th><th className="th">Risk</th><th className="th">Evidence</th><th className="th text-right">Accounts</th><th className="th text-right">Exposure</th><th className="th">Detected</th><th className="th" /></tr></thead>
                  <tbody>
                    {al.data.map((a) => (
                      <tr key={a.alert_id}>
                        <td className="td font-mono text-[12px]"><Link className="text-brand-600 hover:underline" to={`/alerts?open=${a.alert_id}`}>{a.alert_id}</Link></td>
                        <td className="td"><Tag>{label(a.alert_type)}</Tag></td>
                        <td className="td whitespace-nowrap"><BandBadge band={a.risk_band} /> <span className="tabular">{f3(a.risk_score)}</span></td>
                        <td className="td max-w-[420px] text-ink-700">{a.evidence_summary}</td>
                        <td className="td tabular text-right">{a.n_accounts}</td><td className="td tabular text-right">{inr(a.exposed_value, true)}</td>
                        <td className="td whitespace-nowrap tabular">{dt(a.alert_timestamp)}</td>
                        <td className="td"><Link className="btn" to={`/network?account=${a.entity_id}&alert=${a.alert_id}`}>Explore</Link></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </Panel>
    </div>
  )
}
