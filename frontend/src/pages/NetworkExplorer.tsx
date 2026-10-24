import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ColorMode, CytoGraph, edgeKey } from '../components/CytoGraph'
import { BandBadge, Empty, ErrorBox, Field, Notice, Panel, Spinner, StatusBadge, Tag } from '../components/ui'
import { useDataset } from '../hooks/DatasetContext'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { AlertDetail, GraphEdge, GraphNode, Subgraph } from '../types'
import { dt, f3, inr, label, num } from '../utils/format'

interface TracePath { hops: number; accounts: string[]; first_amount: number; last_amount: number; min_to_max_ratio: number; elapsed_hours: number; transactions: { transaction_id: string; source: string; destination: string; timestamp: string; amount: number }[] }
interface Trace { paths: TracePath[]; n_paths: number; truncated: boolean; note: string }
interface AccountDetail {
  account_id: string; account_type: string; created_at: string | null; stats: Record<string, number | string | null>
  top_counterparties: { account_id: string; n_transactions: number }[]
  alerts: { alert_id: string; alert_type: string; risk_band: string; risk_score: number; status: string }[]
  investigations: { investigation_id: string; status: string; priority: string }[]
}

export default function NetworkExplorer() {
  const { datasetId } = useDataset()
  const [sp, setSp] = useSearchParams()
  const [input, setInput] = useState(sp.get('account') || '')
  const [account, setAccount] = useState(sp.get('account') || '')
  const [hops, setHops] = useState(sp.get('alert') ? 2 : 1)
  const [minAmount, setMinAmount] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [maxNodes, setMaxNodes] = useState(120)
  const [colorMode, setColorMode] = useState<ColorMode>('type')
  const [graph, setGraph] = useState<{ nodes: GraphNode[]; edges: GraphEdge[]; truncated: boolean; alerts: Subgraph['alerts_in_view']; ms: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [selNode, setSelNode] = useState<string | null>(null)
  const [selEdge, setSelEdge] = useState<GraphEdge | null>(null)
  const [focus, setFocus] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [hlNodes, setHlNodes] = useState<Set<string>>(new Set())
  const [hlEdges, setHlEdges] = useState<Set<string>>(new Set())
  const [overlayName, setOverlayName] = useState('')
  const [trace, setTrace] = useState<Trace | null>(null)
  const [pathNodes, setPathNodes] = useState<Set<string>>(new Set())
  const [tracing, setTracing] = useState(false)
  const alertParam = sp.get('alert')

  const params = useCallback(() => ({
    dataset_id: datasetId, hops, min_amount: minAmount || undefined, max_nodes: maxNodes,
    date_from: from ? from + 'T00:00:00' : undefined, date_to: to ? to + 'T23:59:59' : undefined,
  }), [datasetId, hops, minAmount, maxNodes, from, to])

  const load = useCallback(async (acc: string) => {
    if (!acc || !datasetId) return
    setBusy(true); setErr(null); setSelNode(null); setSelEdge(null); setTrace(null); setPathNodes(new Set()); setHlNodes(new Set()); setHlEdges(new Set()); setOverlayName('')
    try {
      const g = await api.get<Subgraph>('/graph/subgraph', { account_id: acc, ...params() })
      setGraph({ nodes: g.nodes, edges: g.edges, truncated: g.truncated, alerts: g.alerts_in_view, ms: g.query_ms })
      setAccount(acc)
    } catch (e) {
      setErr((e as Error).message); setGraph(null)
    } finally { setBusy(false) }
  }, [datasetId, params])

  useEffect(() => { const a = sp.get('account'); if (a && datasetId) { setInput(a); load(a) } /* eslint-disable-next-line */ }, [datasetId])

  const applyAlert = useCallback(async (id: string, silent = false) => {
    try {
      const a = await api.get<AlertDetail>(`/alerts/${id}`)
      const nodes = new Set<string>(a.detail.accounts)
      const edges = new Set<string>()
      for (const p of [...a.detail.paths, ...a.detail.detections.map((d) => d.path)]) for (let i = 0; i + 1 < p.length; i++) edges.add(edgeKey(p[i], p[i + 1]))
      for (const t of a.transactions) edges.add(edgeKey(t.source_account_id, t.destination_account_id))
      setHlNodes(nodes); setHlEdges(edges); setOverlayName(`${label(a.alert_type)} · ${a.alert_id}`)
    } catch (e) { if (!silent) setErr((e as Error).message) }
  }, [])
  useEffect(() => { if (alertParam && graph) applyAlert(alertParam, true) /* eslint-disable-next-line */ }, [alertParam, graph?.nodes.length])

  const overlayByType = async (type: string) => {
    if (!graph) return
    const ids = graph.alerts.filter((a) => a.alert_type === type).slice(0, 8)
    if (!ids.length) { setHlNodes(new Set()); setHlEdges(new Set()); setOverlayName(''); return }
    const nodes = new Set<string>(); const edges = new Set<string>()
    for (const x of ids) {
      try {
        const a = await api.get<AlertDetail>(`/alerts/${x.alert_id}`)
        a.detail.accounts.forEach((n) => nodes.add(n))
        for (const p of [...a.detail.paths, ...a.detail.detections.map((d) => d.path)]) for (let i = 0; i + 1 < p.length; i++) edges.add(edgeKey(p[i], p[i + 1]))
        for (const t of a.transactions) edges.add(edgeKey(t.source_account_id, t.destination_account_id))
      } catch { /* skip */ }
    }
    setHlNodes(nodes); setHlEdges(edges); setOverlayName(`${label(type)} (${ids.length} alert${ids.length > 1 ? 's' : ''} in view)`)
  }

  const expand = async () => {
    if (!selNode || !graph) return
    setBusy(true)
    try {
      const g = await api.get<Subgraph>('/graph/subgraph', { account_id: selNode, ...params(), hops: 1 })
      const nm = new Map(graph.nodes.map((n) => [n.id, n]))
      g.nodes.forEach((n) => { if (!nm.has(n.id)) nm.set(n.id, { ...n, hop: (nm.get(selNode)?.hop ?? 0) + 1 }) })
      const em = new Map(graph.edges.map((e) => [edgeKey(e.source, e.target), e]))
      g.edges.forEach((e) => em.set(edgeKey(e.source, e.target), e))
      if (nm.size > 400) { setErr('Graph limit reached (400 nodes). Narrow the filters or start from a different account.'); return }
      setGraph({ ...graph, nodes: [...nm.values()], edges: [...em.values()], truncated: graph.truncated || g.truncated, alerts: [...new Map([...graph.alerts, ...g.alerts_in_view].map((a) => [a.alert_id, a])).values()] })
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const runTrace = async (direction: 'forward' | 'backward') => {
    if (!selNode) return
    setTracing(true); setTrace(null)
    try {
      const t = await api.get<Trace>('/graph/trace', { account_id: selNode, dataset_id: datasetId, direction, max_hops: 4, max_paths: 20, min_amount: minAmount || undefined, date_from: from ? from + 'T00:00:00' : undefined, date_to: to ? to + 'T23:59:59' : undefined, min_flow_fraction: 0.5 })
      setTrace(t)
    } catch (e) { setErr((e as Error).message) } finally { setTracing(false) }
  }

  const detail = useAsync(() => api.get<AccountDetail>(`/accounts/${selNode}`, { dataset_id: datasetId }), [selNode, datasetId], !!selNode)
  const edgeTx = useAsync(() => api.get<{ items: { transaction_id: string; transaction_timestamp: string; amount: number; transaction_type: string }[] }>('/graph/edge', { source: selEdge!.source, target: selEdge!.target, dataset_id: datasetId }), [selEdge?.source, selEdge?.target, datasetId], !!selEdge)

  const nodeIds = useMemo(() => new Set(graph?.nodes.map((n) => n.id)), [graph])
  const doSearch = () => {
    const s = search.trim()
    if (!s) return
    if (nodeIds.has(s)) { setFocus(null); setTimeout(() => { setFocus(s); setSelNode(s) }, 0) } else { setInput(s); load(s) }
  }
  const types = useMemo(() => [...new Set(graph?.alerts.map((a) => a.alert_type))], [graph])

  return (
    <div className="space-y-4">
      <div>
        <h1>Network Explorer</h1>
        <p className="text-ink-500">Directed money-flow graph around an account. Edges aggregate transactions; red outlines mark accounts that appear in alerts (leads, not findings).</p>
      </div>
      <Panel>
        <form className="grid grid-cols-2 gap-3 p-4 md:grid-cols-4 xl:grid-cols-8" onSubmit={(e) => { e.preventDefault(); setSp(input ? { account: input } : {}); load(input.trim()) }} aria-label="Graph query">
          <div className="col-span-2"><Field label="Centre account"><input className="input w-full font-mono" value={input} onChange={(e) => setInput(e.target.value)} placeholder="AC000123" aria-label="Centre account" /></Field></div>
          <Field label="Hop depth"><select className="input w-full" value={hops} onChange={(e) => setHops(+e.target.value)} aria-label="Hop depth">{[1, 2, 3].map((h) => <option key={h}>{h}</option>)}</select></Field>
          <Field label="Min amount (₹)"><input className="input w-full" type="number" min="0" value={minAmount} onChange={(e) => setMinAmount(e.target.value)} /></Field>
          <Field label="From"><input className="input w-full" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
          <Field label="To"><input className="input w-full" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
          <Field label={`Max nodes (${maxNodes})`}><input className="w-full" type="range" min="20" max="400" step="10" value={maxNodes} onChange={(e) => setMaxNodes(+e.target.value)} /></Field>
          <div className="flex items-end"><button className="btn btn-primary" type="submit" disabled={busy || !input.trim()}>{busy ? 'Loading…' : 'Load network'}</button></div>
        </form>
      </Panel>
      {err && <ErrorBox message={err} />}
      {!graph && !busy && !err && <Panel><Empty title="Choose an account to explore" hint="Start from an account in an alert, the Transactions page, or type an ID. The explorer extracts a bounded neighbourhood rather than rendering the entire network." /></Panel>}
      {busy && !graph && <Spinner label="Extracting subgraph…" />}
      {graph && (
        <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
          <div className="space-y-3">
            <Panel
              title={<span>Network around <span className="font-mono">{account}</span> <span className="ml-2 font-normal text-ink-500">{graph.nodes.length} nodes · {graph.edges.length} edges · {graph.ms} ms</span></span>}
              right={
                <div className="flex flex-wrap items-center gap-2">
                  <input className="input !py-1" placeholder="Find account in graph" value={search} onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && doSearch()} aria-label="Find account in graph" />
                  <button className="btn" onClick={doSearch}>Find</button>
                  <select className="input !py-1" value={colorMode} onChange={(e) => setColorMode(e.target.value as ColorMode)} aria-label="Colour nodes by">
                    <option value="type">Colour: account type</option><option value="community">Colour: community</option><option value="risk">Colour: max alert risk</option>
                  </select>
                </div>
              }
            >
              <div className="space-y-2 p-3">
                {graph.truncated && <Notice tone="warn">The neighbourhood was truncated to the heaviest connections ({graph.nodes.length} nodes shown). Raise the minimum amount, shorten the date range or reduce hop depth to focus on a relevant subgraph.</Notice>}
                <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Pattern highlights">
                  <span className="label !mb-0">Highlight</span>
                  <button className="btn" onClick={() => { setHlNodes(new Set()); setHlEdges(new Set()); setOverlayName(''); setPathNodes(new Set()) }}>Clear</button>
                  {[['cycle', 'Circular flows'], ['shared_beneficiary', 'Shared beneficiary'], ['layered_chain', 'Layered chains'], ['coordinated_group', 'Coordinated groups'], ['fan_out', 'Fan-out']].filter(([t]) => types.includes(t)).map(([t, l]) => (
                    <button key={t} className="btn" onClick={() => overlayByType(t)}>{l}</button>
                  ))}
                  {graph.alerts.length > 0 && (
                    <select className="input !py-1" aria-label="Highlight an alert" value="" onChange={(e) => e.target.value && applyAlert(e.target.value)}>
                      <option value="">Alert in view…</option>
                      {graph.alerts.map((a) => <option key={a.alert_id} value={a.alert_id}>{a.alert_id} · {label(a.alert_type)} · {a.risk_band}</option>)}
                    </select>
                  )}
                  {overlayName && <Tag>Highlighting: {overlayName}</Tag>}
                </div>
                <CytoGraph nodes={graph.nodes} edges={graph.edges} centerId={account} colorMode={colorMode} highlightNodes={hlNodes} highlightEdges={hlEdges} pathNodes={pathNodes}
                  onNodeSelect={(id) => { setSelNode(id); if (id) setSelEdge(null) }} onEdgeSelect={(e) => { setSelEdge(e); if (e) setSelNode(null) }} focusId={focus} />
                <Legend colorMode={colorMode} />
              </div>
            </Panel>
            {trace && (
              <Panel title={`Fund-flow trace · ${trace.n_paths} path${trace.n_paths === 1 ? '' : 's'}`} right={<button className="btn" onClick={() => { setTrace(null); setPathNodes(new Set()) }}>Clear trace</button>}>
                <div className="space-y-2 p-3">
                  <Notice tone="warn">{trace.note}{trace.truncated ? ' Results were truncated by path/time limits.' : ''}</Notice>
                  {trace.paths.length === 0 ? <p className="text-ink-500">No temporally-consistent paths found with these settings.</p> : (
                    <div className="max-h-72 overflow-y-auto">
                      {trace.paths.map((p, i) => (
                        <button key={i} className="mb-1 block w-full rounded border border-ink-200 p-2 text-left hover:bg-brand-50" onClick={() => setPathNodes(new Set(p.accounts))}>
                          <div className="font-mono text-[12px]">{p.accounts.join(' → ')}</div>
                          <div className="text-[11px] text-ink-500">{p.hops} hops · {inr(p.first_amount)} → {inr(p.last_amount)} (min/max {f3(p.min_to_max_ratio)}) · {p.elapsed_hours}h elapsed · click to highlight</div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </Panel>
            )}
          </div>
          <div className="space-y-3">
            <Panel title="Selection">
              {!selNode && !selEdge && <p className="p-4 text-ink-500">Click a node or an edge. Selecting a node shows its statistics, linked alerts and investigations; selecting an edge lists the supporting transactions.</p>}
              {selNode && (
                <div className="space-y-3 p-4">
                  <div className="font-mono text-sm font-semibold">{selNode}</div>
                  {detail.loading ? <Spinner /> : detail.error ? <ErrorBox message={detail.error} /> : detail.data && (
                    <>
                      <div className="text-ink-600">{label(detail.data.account_type)} · opened {detail.data.created_at?.slice(0, 10) ?? '—'}</div>
                      <dl className="grid grid-cols-2 gap-2">
                        {[['Outgoing', `${num(detail.data.stats.outgoing_count as number)} · ${inr(detail.data.stats.outgoing_value as number, true)}`], ['Incoming', `${num(detail.data.stats.incoming_count as number)} · ${inr(detail.data.stats.incoming_value as number, true)}`], ['Counterparties', num(detail.data.stats.counterparties as number)], ['Last activity', dt(detail.data.stats.last_activity as string)]].map(([a, b]) => <div key={a}><dt className="label">{a}</dt><dd className="tabular">{b}</dd></div>)}
                      </dl>
                      <div>
                        <div className="label">Linked alerts</div>
                        {detail.data.alerts.length === 0 ? <span className="text-ink-500">none</span> : detail.data.alerts.slice(0, 6).map((a) => (
                          <Link key={a.alert_id} to={`/alerts?open=${a.alert_id}`} className="mb-1 flex items-center gap-2 rounded border border-ink-200 px-2 py-1 hover:bg-ink-50">
                            <BandBadge band={a.risk_band} /><span>{label(a.alert_type)}</span><StatusBadge status={a.status} />
                          </Link>
                        ))}
                      </div>
                      <div>
                        <div className="label">Investigations</div>
                        {detail.data.investigations.length === 0 ? <span className="text-ink-500">none</span> : detail.data.investigations.map((i) => <Link key={i.investigation_id} to={`/investigations/${i.investigation_id}`} className="mr-2 text-brand-600 hover:underline">{i.investigation_id} ({i.status})</Link>)}
                      </div>
                    </>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <button className="btn" onClick={expand} disabled={busy}>Expand neighbours</button>
                    <button className="btn" onClick={() => runTrace('forward')} disabled={tracing}>Trace forward</button>
                    <button className="btn" onClick={() => runTrace('backward')} disabled={tracing}>Trace backward</button>
                    <button className="btn" onClick={() => { setInput(selNode); setSp({ account: selNode }); load(selNode) }}>Re-centre</button>
                    <Link className="btn" to={`/transactions?account=${selNode}`}>Transactions</Link>
                  </div>
                </div>
              )}
              {selEdge && (
                <div className="space-y-2 p-4">
                  <div className="font-mono text-[12px]">{selEdge.source} → {selEdge.target}</div>
                  <div className="text-ink-600">{selEdge.count} transaction{selEdge.count > 1 ? 's' : ''} · total {inr(selEdge.total)} · largest {inr(selEdge.max)}</div>
                  {edgeTx.loading ? <Spinner /> : edgeTx.data && (
                    <div className="max-h-72 overflow-y-auto">
                      <table className="w-full text-[12px]" aria-label="Edge transactions"><thead><tr><th className="th">Transaction</th><th className="th">Time</th><th className="th text-right">Amount</th></tr></thead>
                        <tbody>{edgeTx.data.items.map((t) => <tr key={t.transaction_id}><td className="td font-mono">{t.transaction_id}</td><td className="td tabular">{dt(t.transaction_timestamp)}</td><td className="td tabular text-right">{inr(t.amount)}</td></tr>)}</tbody></table>
                    </div>
                  )}
                </div>
              )}
            </Panel>
            <Notice>Communities (colour mode) are computed on this extract with Louvain: groups of densely connected accounts, not evidence of an organisation.</Notice>
          </div>
        </div>
      )}
    </div>
  )
}

function Legend({ colorMode }: { colorMode: ColorMode }) {
  const items = colorMode === 'type'
    ? [['#8a93a6', 'Individual'], ['#2457c5', 'Business'], ['#0e7490', 'Merchant'], ['#7a5aa6', 'Payment aggregator'], ['#a16207', 'Beneficiary']]
    : colorMode === 'risk' ? [['#b42318', 'Critical'], ['#c2570c', 'High'], ['#a16207', 'Medium'], ['#3f7d58', 'Low'], ['#b8bfcc', 'No alert']] : [['#2457c5', 'Community colours are arbitrary labels']]
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ink-600" aria-label="Legend">
      {items.map(([c, l]) => <span key={l} className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full" style={{ background: c }} />{l}</span>)}
      <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full border-2" style={{ borderColor: '#b42318' }} />Outline: appears in a medium+ alert</span>
      <span className="inline-flex items-center gap-1"><span className="h-0.5 w-5 bg-risk-critical" />Highlighted pattern edge</span>
      <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full border-2 border-ink-900" />Centre</span>
      <span>Arrow = direction of funds; edge width ∝ log(total value)</span>
    </div>
  )
}
