import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Empty, ErrorBox, Field, Pager, Panel, Spinner, StatusBadge } from '../components/ui'
import { useDataset } from '../hooks/DatasetContext'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { Investigation, Page } from '../types'
import { dt } from '../utils/format'

export default function Investigations() {
  const { datasetId } = useDataset()
  const [status, setStatus] = useState('')
  const [priority, setPriority] = useState('')
  const [page, setPage] = useState(1)
  useEffect(() => setPage(1), [status, priority, datasetId])
  const { data, loading, error, reload } = useAsync(() => api.get<Page<Investigation>>('/investigations', { dataset_id: datasetId, status, priority, page, page_size: 25 }), [datasetId, status, priority, page], !!datasetId)
  return (
    <div className="space-y-4">
      <div>
        <h1>Investigations</h1>
        <p className="text-ink-500">Analyst cases opened from alerts. Dispositions are workflow outcomes recorded by reviewers, not legal findings.</p>
      </div>
      <Panel>
        <div className="grid grid-cols-2 gap-3 p-4 md:grid-cols-4">
          <Field label="Status"><select className="input w-full" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status filter"><option value="">All</option>{['New', 'In Review', 'Escalated', 'Resolved', 'Closed'].map((s) => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Priority"><select className="input w-full" value={priority} onChange={(e) => setPriority(e.target.value)} aria-label="Priority filter"><option value="">All</option>{['Critical', 'High', 'Medium', 'Low'].map((s) => <option key={s}>{s}</option>)}</select></Field>
        </div>
      </Panel>
      <Panel>
        {loading && !data ? <Spinner /> : error ? <ErrorBox message={error} onRetry={reload} /> : !data || data.items.length === 0 ? (
          <Empty title="No investigations yet" hint="Open an alert and choose “Create investigation” to start a case." action={<Link className="btn btn-primary" to="/alerts">Go to Alert Center</Link>} />
        ) : (
          <>
            <table className="w-full text-[13px]" aria-label="Investigations">
              <thead><tr><th className="th">Case</th><th className="th">Title</th><th className="th">Status</th><th className="th">Priority</th><th className="th">Analyst</th><th className="th">Alerts</th><th className="th">Disposition</th><th className="th text-right">Age (d)</th><th className="th">Updated</th></tr></thead>
              <tbody>
                {data.items.map((i) => (
                  <tr key={i.investigation_id} className="hover:bg-brand-50">
                    <td className="td font-mono text-[12px]"><Link className="text-brand-600 hover:underline" to={`/investigations/${i.investigation_id}`}>{i.investigation_id}</Link></td>
                    <td className="td">{i.title}</td><td className="td"><StatusBadge status={i.status} /></td><td className="td">{i.priority}</td>
                    <td className="td">{i.assigned_analyst ?? <span className="text-ink-400">unassigned</span>}</td><td className="td tabular">{i.alert_ids.length}</td>
                    <td className="td">{i.disposition ?? <span className="text-ink-400">—</span>}</td><td className="td tabular text-right">{i.age_days}</td><td className="td whitespace-nowrap tabular">{dt(i.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager page={page} pageSize={25} total={data.total} onPage={setPage} />
          </>
        )}
      </Panel>
    </div>
  )
}
