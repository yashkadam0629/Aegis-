import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AlertDetailView } from '../components/AlertDetailView'
import { CytoGraph, edgeKey } from '../components/CytoGraph'
import { EChart, axis } from '../components/EChart'
import { BandBadge, Drawer, Empty, ErrorBox, Field, Notice, Panel, Spinner, StatusBadge } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { AlertDetail, Investigation, Subgraph } from '../types'
import { dt, f3, inr } from '../utils/format'

interface Meta { statuses: string[]; priorities: string[]; dispositions: string[]; transitions: Record<string, string[]> }

export default function InvestigationWorkspace() {
  const { id } = useParams()
  const inv = useAsync(() => api.get<Investigation>(`/investigations/${id}`), [id])
  const meta = useAsync(() => api.get<Meta>('/investigations/meta'), [])
  const primaryAlert = inv.data?.alert_ids[0]
  const alert = useAsync(() => api.get<AlertDetail>(`/alerts/${primaryAlert}`), [primaryAlert], !!primaryAlert)
  const graph = useAsync(() => api.get<Subgraph>('/graph/subgraph', { account_id: alert.data!.entity_id, dataset_id: inv.data!.dataset_id, hops: 1, max_nodes: 60 }), [alert.data?.entity_id], !!alert.data && !!inv.data)
  const [note, setNote] = useState('')
  const [findings, setFindings] = useState<string | null>(null)
  const [disp, setDisp] = useState<string | null>(null)
  const [assignee, setAssignee] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [openAlert, setOpenAlert] = useState<string | null>(null)

  const act = async (fn: () => Promise<unknown>, okText: string) => {
    setBusy(true); setMsg(null)
    try { await fn(); setMsg({ ok: true, text: okText }); inv.reload() } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(false) }
  }

  const highlight = useMemo(() => {
    const n = new Set<string>(alert.data?.detail.accounts ?? [])
    const e = new Set<string>()
    for (const p of alert.data?.detail.paths ?? []) for (let i = 0; i + 1 < p.length; i++) e.add(edgeKey(p[i], p[i + 1]))
    for (const t of alert.data?.transactions ?? []) e.add(edgeKey(t.source_account_id, t.destination_account_id))
    return { n, e }
  }, [alert.data])

  if (inv.loading && !inv.data) return <Spinner />
  if (inv.error) return <ErrorBox message={inv.error} onRetry={inv.reload} />
  const i = inv.data
  if (!i) return null
  const closed = i.status === 'Closed'
  const stepIdx = meta.data ? meta.data.statuses.indexOf(i.status) : 0
  const txs = alert.data?.transactions ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/investigations" className="text-brand-600 hover:underline">← Investigations</Link>
          <h1 className="mt-1">{i.title}</h1>
          <p className="text-ink-500"><span className="font-mono">{i.investigation_id}</span> · opened {dt(i.created_at)} by {i.created_by} · age {i.age_days} d</p>
        </div>
        <div className="flex items-center gap-2"><StatusBadge status={i.status} /><span className="rounded border border-ink-200 px-2 py-0.5">{i.priority} priority</span></div>
      </div>

      {meta.data && (
        <ol className="flex flex-wrap gap-2" aria-label="Case progress">
          {meta.data.statuses.map((s, idx) => <li key={s} aria-current={s === i.status} className={`rounded px-2.5 py-1 text-[12px] ${s === i.status ? 'bg-brand-600 font-medium text-white' : idx < stepIdx ? 'bg-ink-200 text-ink-700' : 'bg-ink-100 text-ink-400'}`}>{idx + 1}. {s}</li>)}
        </ol>
      )}
      {msg && <div role={msg.ok ? 'status' : 'alert'} className={`rounded border px-3 py-2 ${msg.ok ? 'border-green-200 bg-green-50 text-green-900' : 'border-red-200 bg-red-50 text-risk-critical'}`}>{msg.text}</div>}

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Panel title="Case summary & evidence">
            <div className="space-y-3 p-4">
              {alert.data ? (
                <>
                  <div className="flex flex-wrap items-center gap-2"><BandBadge band={alert.data.risk_band} /><span className="tabular font-semibold">{f3(alert.data.risk_score)}</span><span className="text-ink-500">model-generated risk score</span></div>
                  <p>{alert.data.detail.evidence_summary}</p>
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    {[['Entity', alert.data.entity_id], ['Accounts', alert.data.n_accounts], ['Transactions', alert.data.n_transactions], ['Potential exposure', inr(alert.data.exposed_value, true)]].map(([a, b]) => <div key={String(a)}><div className="label">{a}</div><div className="tabular">{b}</div></div>)}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {i.alert_ids.map((a) => <button key={a} className="btn" onClick={() => setOpenAlert(a)}>Alert {a}</button>)}
                    <Link className="btn" to={`/network?account=${alert.data.entity_id}&alert=${alert.data.alert_id}`}>Open in Network Explorer</Link>
                  </div>
                </>
              ) : alert.loading ? <Spinner /> : <Empty title="No linked alert" />}
            </div>
          </Panel>

          <Panel title="Transaction timeline">
            <div className="p-3">
              {txs.length === 0 ? <Empty title="No transactions attached" /> : (
                <EChart ariaLabel="Scatter plot of evidence transactions over time by amount" height={240}
                  option={{
                    grid: { left: 70, right: 20, top: 14, bottom: 30 }, tooltip: { trigger: 'item', formatter: (p: { data: [number, number, string] }) => `${p.data[2]}<br/>${inr(p.data[1])}` },
                    xAxis: { type: 'time', ...axis() }, yAxis: { type: 'log', name: 'amount (₹)', ...axis() },
                    series: [{ type: 'scatter', symbolSize: 9, data: txs.map((t) => [t.transaction_timestamp + 'Z', t.amount, `${t.source_account_id} → ${t.destination_account_id}`]) }],
                  }} />
              )}
            </div>
          </Panel>

          <Panel title="Relevant network">
            <div className="p-3">
              {graph.loading ? <Spinner label="Loading network…" /> : graph.data ? (
                <CytoGraph nodes={graph.data.nodes} edges={graph.data.edges} centerId={alert.data?.entity_id} colorMode="risk" highlightNodes={highlight.n} highlightEdges={highlight.e} height={320} />
              ) : <Empty title="Network unavailable" />}
            </div>
          </Panel>

          <Panel title={`Attached transactions (${i.transaction_ids?.length ?? 0})`}>
            <div className="max-h-56 overflow-y-auto p-4 font-mono text-[12px] leading-6 text-ink-700">{(i.transaction_ids ?? []).join('  ') || '—'}</div>
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="Case controls">
            <div className="space-y-3 p-4">
              <div>
                <span className="label">Status</span>
                <div className="flex flex-wrap gap-2">
                  {(i.allowed_transitions ?? []).length === 0 ? <span className="text-ink-500">Closed – read-only</span> : i.allowed_transitions!.map((s) => (
                    <button key={s} className="btn" disabled={busy} onClick={() => act(() => api.patch(`/investigations/${i.investigation_id}`, { status: s, ...(disp ? { disposition: disp } : {}), ...(findings ? { findings } : {}) }), `Status changed to ${s}`)}>Move to {s}</button>
                  ))}
                </div>
              </div>
              <Field label="Priority">
                <select className="input w-full" disabled={closed} value={i.priority} onChange={(e) => act(() => api.patch(`/investigations/${i.investigation_id}`, { priority: e.target.value }), 'Priority updated')} aria-label="Priority">
                  {['Low', 'Medium', 'High', 'Critical'].map((p) => <option key={p}>{p}</option>)}
                </select>
              </Field>
              <Field label="Assigned analyst">
                <div className="flex gap-2">
                  <input className="input min-w-0 flex-1" disabled={closed} value={assignee ?? i.assigned_analyst ?? ''} onChange={(e) => setAssignee(e.target.value)} aria-label="Assigned analyst" />
                  <button className="btn" disabled={closed || busy || assignee === null} onClick={() => act(() => api.patch(`/investigations/${i.investigation_id}`, { assigned_analyst: assignee }), 'Assignment saved').then(() => setAssignee(null))}>Save</button>
                </div>
              </Field>
              <Field label="Disposition (analyst outcome, not a legal finding)">
                <select className="input w-full" disabled={closed} value={disp ?? i.disposition ?? ''} onChange={(e) => setDisp(e.target.value)} aria-label="Disposition">
                  <option value="">— not set —</option>{meta.data?.dispositions.map((d) => <option key={d}>{d}</option>)}
                </select>
              </Field>
              <Field label="Findings">
                <textarea className="input h-24 w-full" disabled={closed} value={findings ?? i.findings ?? ''} onChange={(e) => setFindings(e.target.value)} aria-label="Findings" />
              </Field>
              <button className="btn" disabled={closed || busy || (disp === null && findings === null)} onClick={() => act(() => api.patch(`/investigations/${i.investigation_id}`, { ...(disp !== null ? { disposition: disp } : {}), ...(findings !== null ? { findings } : {}) }), 'Disposition and findings saved')}>Save findings</button>
              <Notice>{i.disposition_notice} Re-running the model never changes this case or its review decision.</Notice>
            </div>
          </Panel>

          <Panel title="Reviewer notes">
            <div className="space-y-3 p-4">
              {(i.notes ?? []).length === 0 && <p className="text-ink-500">No notes yet.</p>}
              {(i.notes ?? []).map((n) => <div key={n.note_id} className="rounded border border-ink-200 p-2.5"><div className="text-[11px] text-ink-500">{n.author} · {dt(n.created_at)}</div><div className="whitespace-pre-wrap">{n.body}</div></div>)}
              {!closed && (
                <form onSubmit={(e) => { e.preventDefault(); if (note.trim()) act(() => api.post(`/investigations/${i.investigation_id}/notes`, { body: note }), 'Note added').then(() => setNote('')) }}>
                  <textarea className="input h-20 w-full" placeholder="Add a reviewer note…" value={note} onChange={(e) => setNote(e.target.value)} aria-label="New note" />
                  <button className="btn btn-primary mt-2" type="submit" disabled={busy || !note.trim()}>Add note</button>
                </form>
              )}
            </div>
          </Panel>

          <Panel title="Status history (audit trail)">
            <ol className="max-h-80 space-y-2 overflow-y-auto p-4">
              {(i.history ?? []).slice().reverse().map((h) => (
                <li key={h.audit_id} className="border-l-2 border-ink-200 pl-3">
                  <div className="text-[11px] text-ink-500">{dt(h.at)} · {h.actor}</div>
                  <div>{h.action.replace('_', ' ')}{h.field ? <> — <b>{h.field}</b></> : null}{h.old !== null || h.new !== null ? <>: <span className="text-ink-500">{h.old ?? '∅'}</span> → {h.new ?? '∅'}</> : null}</div>
                </li>
              ))}
            </ol>
          </Panel>
        </div>
      </div>
      <Drawer open={!!openAlert} width={760} onClose={() => setOpenAlert(null)} title={<div className="font-mono text-sm font-semibold">{openAlert}</div>}>
        {openAlert && <AlertDetailView alertId={openAlert} />}
      </Drawer>
    </div>
  )
}
