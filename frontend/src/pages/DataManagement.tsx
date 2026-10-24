import { useState } from 'react'
import { JobButton } from '../components/JobButton'
import { ErrorBox, Field, Notice, Panel, Spinner, Tag } from '../components/ui'
import { useDataset } from '../hooks/DatasetContext'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import { d0, dt, label, num } from '../utils/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any
const CANON = ['transaction_id', 'source_account_id', 'destination_account_id', 'transaction_timestamp', 'amount', 'currency', 'transaction_type', 'channel']
const REQUIRED = new Set(['transaction_id', 'source_account_id', 'destination_account_id', 'transaction_timestamp', 'amount'])

export default function DataManagement() {
  const { datasets, datasetId, setDatasetId, refresh } = useDataset()
  const imports = useAsync(() => api.get<{ items: Any[] }>('/imports'), [datasets.length])
  const [file, setFile] = useState<File | null>(null)
  const [accFile, setAccFile] = useState<File | null>(null)
  const [schema, setSchema] = useState<Any | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [name, setName] = useState('')
  const [result, setResult] = useState<Any | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const inspect = async (f: File) => {
    setFile(f); setSchema(null); setResult(null); setErr(null)
    const fd = new FormData(); fd.append('file', f)
    try {
      const s = await api.upload<Any>('/imports/inspect', fd)
      setSchema(s); setMapping(Object.fromEntries(Object.entries(s.suggested_mapping).filter(([, v]) => v).map(([k, v]) => [k, v as string]))); setName(f.name.replace(/\.csv$/i, ''))
    } catch (e) { setErr((e as Error).message) }
  }
  const run = async () => {
    if (!file) return
    setBusy(true); setErr(null)
    const fd = new FormData()
    fd.append('file', file); fd.append('name', name); fd.append('mapping', JSON.stringify(mapping))
    if (accFile) fd.append('accounts_file', accFile)
    try { const r = await api.upload<Any>('/imports', fd); setResult(r); refresh(); imports.reload() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const missing = CANON.filter((c) => REQUIRED.has(c) && !mapping[c])

  return (
    <div className="space-y-4">
      <div>
        <h1>Data Management</h1>
        <p className="text-ink-500">Import CSV data, generate the synthetic demonstration dataset, and run the analysis pipeline. Uploaded files are treated as untrusted input: parsed, validated, never executed.</p>
      </div>

      <Panel title="Datasets">
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]" aria-label="Datasets">
            <thead><tr><th className="th">Name</th><th className="th">Kind</th><th className="th text-right">Transactions</th><th className="th text-right">Accounts</th><th className="th">Period</th><th className="th">Version</th><th className="th" /></tr></thead>
            <tbody>
              {datasets.length === 0 && <tr><td className="td text-ink-500" colSpan={7}>No datasets yet – generate the synthetic dataset below or import a CSV.</td></tr>}
              {datasets.map((d) => (
                <tr key={d.dataset_id} className={d.dataset_id === datasetId ? 'bg-brand-50' : ''}>
                  <td className="td">{d.name}<div className="font-mono text-[11px] text-ink-500">{d.dataset_id}</div></td>
                  <td className="td">{d.kind === 'synthetic' ? <Tag>synthetic</Tag> : <Tag>upload</Tag>}{d.has_ground_truth && <span className="ml-1 text-[11px] text-ink-500">labels held out</span>}</td>
                  <td className="td tabular text-right">{num(d.n_transactions)}</td><td className="td tabular text-right">{num(d.n_accounts)}</td>
                  <td className="td whitespace-nowrap">{d0(d.min_ts)} → {d0(d.max_ts)}</td><td className="td font-mono text-[12px]">{d.dataset_version}</td>
                  <td className="td whitespace-nowrap">
                    <button className="btn" onClick={() => setDatasetId(d.dataset_id)} disabled={d.dataset_id === datasetId}>{d.dataset_id === datasetId ? 'Selected' : 'Select'}</button>
                    <button className="btn btn-danger ml-2" onClick={async () => { if (window.confirm(`Permanently delete "${d.name}" and all derived alerts, scores and investigations?`)) { await api.del(`/datasets/${d.dataset_id}`); refresh() } }}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Synthetic demonstration data & analysis">
          <div className="space-y-4 p-4">
            <Notice>Synthetic data contains simulated scenarios (layering, cycles, shared beneficiaries, bursts, …) <b>and benign look-alikes</b>. Labels are held out for evaluation only.</Notice>
            <div className="flex flex-wrap items-start gap-4">
              <JobButton label="Generate quick dataset (~12k txns)" path="/datasets/synthetic" params={{ quick: true }} onDone={() => refresh()} />
              <JobButton label="Generate standard dataset (~72k txns)" path="/datasets/synthetic" params={{ quick: false }} onDone={() => refresh()} />
            </div>
            <div className="flex flex-wrap items-start gap-4 border-t border-ink-100 pt-4">
              <JobButton label="1. Train model (chronological)" path="/models/train" params={{ dataset_id: datasetId }} disabled={!datasetId} />
              <JobButton label="2. Run analysis pipeline" path="/pipeline/run" params={{ dataset_id: datasetId }} primary disabled={!datasetId} onDone={() => refresh()} />
            </div>
            <p className="text-[12px] text-ink-500">Training scores several models per feature group and takes about a minute on the standard dataset; the pipeline recomputes features, runs the graph detectors and writes alerts. Existing investigations are never overwritten.</p>
          </div>
        </Panel>

        <Panel title="Import CSV">
          <div className="space-y-3 p-4">
            <Field label="Transactions file (.csv)"><input type="file" accept=".csv" aria-label="Transactions CSV" onChange={(e) => e.target.files?.[0] && inspect(e.target.files[0])} /></Field>
            {err && <ErrorBox message={err} />}
            {schema && (
              <>
                <div className="text-ink-600">{num(schema.n_rows)} rows (first 50,000 inspected) · {schema.columns.length} columns</div>
                <div className="grid grid-cols-2 gap-2">
                  {CANON.map((c) => (
                    <label key={c} className="block">
                      <span className="label">{label(c)}{REQUIRED.has(c) && ' *'}</span>
                      <select className="input w-full" value={mapping[c] ?? ''} onChange={(e) => setMapping({ ...mapping, [c]: e.target.value })} aria-label={`Map ${c}`}>
                        <option value="">— none —</option>{schema.columns.map((col: Any) => <option key={col.name} value={col.name}>{col.name} ({col.inferred_type})</option>)}
                      </select>
                    </label>
                  ))}
                </div>
                <details className="text-[12px]"><summary className="cursor-pointer text-brand-600">Preview of detected columns</summary>
                  <table className="mt-1 w-full"><thead><tr><th className="th">Column</th><th className="th">Type</th><th className="th">Nulls</th><th className="th">Sample</th></tr></thead><tbody>{schema.columns.map((c: Any) => <tr key={c.name}><td className="td">{c.name}</td><td className="td">{c.inferred_type}</td><td className="td">{(c.null_fraction * 100).toFixed(1)}%</td><td className="td font-mono">{c.sample.slice(0, 3).join(', ')}</td></tr>)}</tbody></table></details>
                <Field label="Optional accounts file (account_id, account_type, created_at)"><input type="file" accept=".csv" aria-label="Accounts CSV" onChange={(e) => setAccFile(e.target.files?.[0] ?? null)} /></Field>
                <Field label="Dataset name"><input className="input w-full" value={name} onChange={(e) => setName(e.target.value)} /></Field>
                {missing.length > 0 && <Notice tone="warn">Map required fields: {missing.map(label).join(', ')}.</Notice>}
                <button className="btn btn-primary" disabled={busy || missing.length > 0} onClick={run}>{busy ? 'Importing…' : 'Validate & import'}</button>
              </>
            )}
            {result && <ImportReport r={result} />}
          </div>
        </Panel>
      </div>

      <Panel title="Import history & data-quality reports">
        {imports.loading && !imports.data ? <Spinner /> : imports.error ? <ErrorBox message={imports.error} /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]" aria-label="Import history">
              <thead><tr><th className="th">Report</th><th className="th">File</th><th className="th">When</th><th className="th">Status</th><th className="th text-right">Received</th><th className="th text-right">Accepted</th><th className="th text-right">Rejected</th><th className="th">Rejected by reason</th><th className="th" /></tr></thead>
              <tbody>
                {(imports.data?.items ?? []).map((r) => (
                  <tr key={r.report_id}>
                    <td className="td font-mono text-[12px]">{r.report_id}</td><td className="td">{r.filename}</td><td className="td whitespace-nowrap tabular">{dt(r.created_at)}</td><td className="td">{r.status}</td>
                    <td className="td tabular text-right">{num(r.report.rows_received)}</td><td className="td tabular text-right">{num(r.report.rows_accepted)}</td><td className="td tabular text-right">{num(r.report.rows_rejected)}</td>
                    <td className="td text-[12px]">{Object.entries(r.report.rejected_by_reason || {}).map(([k, v]) => `${label(k)}: ${v}`).join(', ') || r.report.fatal_error || '—'}</td>
                    <td className="td">{r.has_error_report && <a className="btn" href={`/api/imports/${r.report_id}/errors`} download>Download error report</a>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}

function ImportReport({ r }: { r: Any }) {
  const rep = r.report
  return (
    <div className="space-y-2 rounded border border-ink-200 p-3" aria-label="Import result">
      <div className="font-medium">{r.status === 'failed' ? 'Import failed' : 'Import completed'} · {r.report_id}</div>
      {rep.fatal_error && <div className="text-risk-critical">{rep.fatal_error}</div>}
      <dl className="grid grid-cols-3 gap-2">
        {[['Received', rep.rows_received], ['Accepted', rep.rows_accepted], ['Rejected', rep.rows_rejected], ['Exact duplicates dropped', rep.duplicate_records?.exact_duplicates_dropped], ['Conflicting duplicates', rep.duplicate_records?.conflicting_duplicates_rejected], ['Unresolved accounts', rep.unresolved_account_references], ['Invalid timestamps', rep.invalid_timestamps], ['Invalid amounts', rep.invalid_amounts], ['Invalid currencies', rep.invalid_currency]].map(([a, b]) => <div key={String(a)}><dt className="label">{a}</dt><dd className="tabular">{b === undefined ? '—' : num(b as number)}</dd></div>)}
      </dl>
      {rep.handling && <details className="text-[12px]"><summary className="cursor-pointer text-brand-600">How each problem is handled</summary><ul className="mt-1 list-disc pl-5">{Object.entries(rep.handling).map(([k, v]) => <li key={k}><b>{label(k)}</b>: {String(v)}</li>)}</ul></details>}
      {r.has_error_report && <a className="btn" href={`/api/imports/${r.report_id}/errors`} download>Download rejected rows (CSV)</a>}
    </div>
  )
}
