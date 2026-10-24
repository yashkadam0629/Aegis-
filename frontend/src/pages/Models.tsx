import { useState } from 'react'
import { EChart, axis } from '../components/EChart'
import { Empty, ErrorBox, Notice, Panel, Spinner, Tabs, Tag } from '../components/ui'
import { useDataset } from '../hooks/DatasetContext'
import { JobButton } from '../components/JobButton'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import { d0, dt, f3, inr, label, num, pct } from '../utils/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any

export default function Models() {
  const { datasetId } = useDataset()
  const [tab, setTab] = useState('eval')
  const [runId, setRunId] = useState<string | null>(null)
  const list = useAsync(() => api.get<{ items: Any[] }>('/models'), [])
  const active = list.data?.items.find((r) => r.is_active)
  const sel = runId ?? active?.model_run_id
  const run = useAsync(() => api.get<Any>(`/models/${sel}/metrics`), [sel], !!sel)

  if (list.loading && !list.data) return <Spinner />
  if (list.error) return <ErrorBox message={list.error} onRetry={list.reload} />
  if (!list.data || list.data.items.length === 0) return <Empty title="No models trained" hint="Train a model from Data Management (requires a labelled synthetic dataset)." />
  const em = run.data?.evaluation_metrics

  return (
    <div className="space-y-4">
      <div>
        <h1>Model Performance</h1>
        <p className="text-ink-500">All metrics come from stored evaluation runs on <b>synthetic</b> scenario labels. Training, validation and test results are shown separately.</p>
      </div>
      <Panel title="Model versions" right={<span className="text-ink-500">Promotion is a manual action; drift never promotes a model.</span>}>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]" aria-label="Model versions">
            <thead><tr><th className="th">Run</th><th className="th">Version</th><th className="th">Selected</th><th className="th text-right">Val PR-AUC</th><th className="th text-right">Test PR-AUC</th><th className="th">Trained</th><th className="th">Dataset</th><th className="th" /></tr></thead>
            <tbody>
              {list.data.items.map((r) => (
                <tr key={r.model_run_id} className={r.model_run_id === sel ? 'bg-brand-50' : ''}>
                  <td className="td font-mono text-[12px]">{r.model_run_id} {r.is_active && <Tag>active</Tag>}</td><td className="td">{r.model_version}</td><td className="td">{r.summary?.selected}</td>
                  <td className="td tabular text-right">{f3(r.summary?.validation_pr_auc)}</td><td className="td tabular text-right">{f3(r.summary?.test_pr_auc)}</td><td className="td whitespace-nowrap">{dt(r.training_timestamp)}</td>
                  <td className="td font-mono text-[12px]">{r.dataset_id} · {r.dataset_version}</td>
                  <td className="td whitespace-nowrap"><button className="btn" onClick={() => setRunId(r.model_run_id)}>View</button>{!r.is_active && <button className="btn ml-2" onClick={async () => { await api.post(`/models/${r.model_run_id}/activate`); list.reload() }}>Activate</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel>
        <Tabs tabs={[{ id: 'eval', label: 'Evaluation' }, { id: 'thresholds', label: 'Thresholds & calibration' }, { id: 'ops', label: 'Monitoring & drift' }, { id: 'impact', label: 'Business-impact benchmark' }]} value={tab} onChange={setTab} />
        {run.loading && !em ? <Spinner /> : run.error ? <ErrorBox message={run.error} /> : em && (
          <div className="p-4">
            {tab === 'eval' && <Evaluation em={em} />}
            {tab === 'thresholds' && <Thresholds em={em} />}
            {tab === 'ops' && <Monitoring datasetId={datasetId} />}
            {tab === 'impact' && <Impact />}
          </div>
        )}
      </Panel>
    </div>
  )
}

function Evaluation({ em }: { em: Any }) {
  const sp = em.split
  const models: Record<string, Any> = em.results.models
  const groups = ['A_transaction', 'B_behavioral', 'C_graph', 'D_combined']
  const kinds = ['logreg', 'random_forest', 'lightgbm']
  const hold = em.results.holdout_templates || {}
  return (
    <div className="space-y-5">
      <Notice>
        <b>Method.</b> {sp.method} Train {d0(sp.train_start)} → {d0(sp.train_end)} ({num(sp.rows.train)} rows, {num(sp.positives.train)} positive; {num(sp.rows.train_purged)} purged), validation → {d0(sp.validation_end)} ({num(sp.rows.validation)} rows),
        test → {d0(sp.test_end)} ({num(sp.rows.test)} rows, {num(sp.positives.test)} positive). Model selected by <b>validation</b> PR-AUC: <b>{em.selected_model}</b>.
      </Notice>
      <div>
        <h2 className="mb-2">Feature-group comparison – test PR-AUC (higher is better)</h2>
        <EChart ariaLabel="Grouped bar chart of test PR-AUC by feature group and algorithm" height={280}
          option={{
            grid: { left: 50, right: 10, top: 36, bottom: 30 }, legend: { top: 0, textStyle: { color: '#4b556b' } }, tooltip: { trigger: 'axis' },
            xAxis: { type: 'category', data: groups.map((g) => label(g.replace('_', ' – '))), ...axis() }, yAxis: { type: 'value', min: 0, max: 1, ...axis() },
            series: [...kinds.map((k) => ({ name: label(k), type: 'bar', data: groups.map((g) => +(models[`${g}/${k}`]?.test.pr_auc ?? 0).toFixed(3)) })),
              { name: 'Prevalence', type: 'line', data: groups.map(() => +sp.positives.test / sp.rows.test), symbol: 'none', lineStyle: { type: 'dashed', color: '#657089' } }],
          }} />
        <p className="text-[12px] text-ink-500">Graph and behavioural features are compared with the transaction-only baseline (A) on the same chronological test period. Differences between close models are within sampling noise — see the 95% bootstrap intervals below. Amount alone separates many simulated scenarios, so model A is a stronger baseline than it would be on real data.</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]" aria-label="Model comparison">
          <thead><tr><th className="th">Features / algorithm</th><th className="th text-right">Train PR-AUC</th><th className="th text-right">Val PR-AUC</th><th className="th text-right">Test PR-AUC</th><th className="th text-right">Test 95% CI</th><th className="th text-right">Test ROC-AUC</th><th className="th text-right">Precision @1%</th><th className="th text-right">Recall @1%</th><th className="th text-right">Recall @2%</th></tr></thead>
          <tbody>
            {Object.entries(models).map(([k, v]) => (
              <tr key={k} className={k === em.selected_model ? 'bg-brand-50 font-medium' : ''}>
                <td className="td">{k}{k === em.selected_model && ' ✓'}</td><td className="td tabular text-right">{f3(v.train.pr_auc)}</td><td className="td tabular text-right">{f3(v.validation.pr_auc)}</td><td className="td tabular text-right">{f3(v.test.pr_auc)}</td>
                <td className="td tabular text-right">{v.test.pr_auc_ci95 ? `${f3(v.test.pr_auc_ci95[0])}–${f3(v.test.pr_auc_ci95[1])}` : '—'}</td><td className="td tabular text-right">{f3(v.test.roc_auc)}</td>
                <td className="td tabular text-right">{pct(v.test.capacity['0.010'].precision)}</td><td className="td tabular text-right">{pct(v.test.capacity['0.010'].recall)}</td><td className="td tabular text-right">{pct(v.test.capacity['0.020'].recall)}</td>
              </tr>
            ))}
            <tr className="border-t-2 border-ink-200"><td className="td">Rules baseline (no ML)</td><td className="td tabular text-right">{f3(em.results.rules_baseline.train.pr_auc)}</td><td className="td tabular text-right">{f3(em.results.rules_baseline.validation.pr_auc)}</td><td className="td tabular text-right">{f3(em.results.rules_baseline.test.pr_auc)}</td><td className="td" /><td className="td tabular text-right">{f3(em.results.rules_baseline.test.roc_auc)}</td><td className="td tabular text-right">{pct(em.results.rules_baseline.test.capacity['0.010'].precision)}</td><td className="td tabular text-right">{pct(em.results.rules_baseline.test.capacity['0.010'].recall)}</td><td className="td tabular text-right">{pct(em.results.rules_baseline.test.capacity['0.020'].recall)}</td></tr>
            <tr><td className="td">Unsupervised IsolationForest (no labels)</td><td className="td tabular text-right">{f3(em.results.unsupervised.train.pr_auc)}</td><td className="td tabular text-right">{f3(em.results.unsupervised.validation.pr_auc)}</td><td className="td tabular text-right">{f3(em.results.unsupervised.test.pr_auc)}</td><td className="td" /><td className="td tabular text-right">{f3(em.results.unsupervised.test.roc_auc)}</td><td className="td tabular text-right">{pct(em.results.unsupervised.test.capacity['0.010'].precision)}</td><td className="td tabular text-right">{pct(em.results.unsupervised.test.capacity['0.010'].recall)}</td><td className="td tabular text-right">{pct(em.results.unsupervised.test.capacity['0.020'].recall)}</td></tr>
          </tbody>
        </table>
      </div>
      <div>
        <h2 className="mb-1">Stronger check – scenario templates held out</h2>
        <p className="mb-2 text-[12px] text-ink-500">{em.results.holdout_description}</p>
        <EChart ariaLabel="Bar chart of PR-AUC when scenario template variants are held out" height={220}
          option={{ grid: { left: 50, right: 10, top: 10, bottom: 40 }, tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: Object.keys(hold).map((k) => k.replace('/lightgbm', '')), ...axis({ axisLabel: { color: '#4b556b', fontSize: 11, interval: 0 } }) }, yAxis: { type: 'value', min: 0, max: 1, ...axis() },
            series: [{ type: 'bar', barWidth: '45%', data: Object.values(hold).map((v: Any) => +(v.pr_auc ?? 0).toFixed(3)) }] }} />
      </div>
      {em.performance_over_time?.length > 0 && (
        <div>
          <h2 className="mb-1">Performance over time (weekly, test period)</h2>
          <EChart ariaLabel="Line chart of weekly PR-AUC in the test period" height={200}
            option={{ grid: { left: 50, right: 10, top: 10, bottom: 28 }, tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: em.performance_over_time.map((w: Any) => w.week.slice(0, 10)), ...axis() }, yAxis: { type: 'value', min: 0, max: 1, ...axis() },
              series: [{ type: 'line', data: em.performance_over_time.map((w: Any) => +w.pr_auc.toFixed(3)), symbolSize: 7 }] }} />
          <p className="text-[12px] text-ink-500">Weeks with fewer than 3 positive transactions are omitted; weekly estimates are noisy.</p>
        </div>
      )}
      {Object.keys(em.feature_importance || {}).length > 0 && (
        <div>
          <h2 className="mb-1">Feature importance (selected model, normalised split gain)</h2>
          <EChart ariaLabel="Bar chart of the top feature importances" height={Math.min(520, 20 * Object.keys(em.feature_importance).length + 40)}
            option={{ grid: { left: 150, right: 20, top: 6, bottom: 24 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }, xAxis: { type: 'value', ...axis() }, yAxis: { type: 'category', inverse: true, data: Object.keys(em.feature_importance).slice(0, 15), ...axis() },
              series: [{ type: 'bar', barWidth: '60%', data: Object.values(em.feature_importance).slice(0, 15) }] }} />
          <p className="text-[12px] text-ink-500">Importance describes model usage, not causal effect.</p>
        </div>
      )}
      <div>
        <h2 className="mb-1">Limitations</h2>
        <ul className="list-disc space-y-1 pl-5 text-ink-600">{em.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
      </div>
    </div>
  )
}

function Thresholds({ em }: { em: Any }) {
  const th = em.thresholds
  const cal = em.calibration
  const rows: [string, Any][] = [['F1-optimal (chosen on validation)', th.f1_optimal_validation], ['Cost-optimal (validation, assumed costs)', th.cost_optimal_validation], ['Top 1% review capacity', th.top_1pct_capacity]]
  return (
    <div className="space-y-5">
      <div>
        <h2 className="mb-2">Threshold comparison on the test period</h2>
        <div className="overflow-x-auto"><table className="w-full text-[12.5px]" aria-label="Threshold comparison">
          <thead><tr><th className="th">Rule</th><th className="th text-right">Threshold</th><th className="th text-right">Alerts</th><th className="th text-right">TP</th><th className="th text-right">FP</th><th className="th text-right">FN</th><th className="th text-right">TN</th><th className="th text-right">Precision</th><th className="th text-right">Recall</th><th className="th text-right">F1</th><th className="th text-right">FPR</th><th className="th text-right">FNR</th></tr></thead>
          <tbody>{rows.map(([n, v]) => <tr key={n}><td className="td">{n}</td><td className="td tabular text-right">{f3(v.threshold)}</td><td className="td tabular text-right">{num(v.n_alerts)}</td><td className="td tabular text-right">{num(v.tp)}</td><td className="td tabular text-right">{num(v.fp)}</td><td className="td tabular text-right">{num(v.fn)}</td><td className="td tabular text-right">{num(v.tn)}</td><td className="td tabular text-right">{pct(v.precision)}</td><td className="td tabular text-right">{pct(v.recall)}</td><td className="td tabular text-right">{f3(v.f1)}</td><td className="td tabular text-right">{pct(v.fpr, 2)}</td><td className="td tabular text-right">{pct(v.fnr)}</td></tr>)}</tbody></table></div>
        <div className="mt-3 grid grid-cols-2 gap-3 md:max-w-lg">
          {['TP', 'FP', 'FN', 'TN'].map((k) => <div key={k} className="panel p-3 text-center"><div className="label">{{ TP: 'True positive', FP: 'False positive', FN: 'False negative', TN: 'True negative' }[k]}</div><div className="tabular text-lg font-semibold">{num(th.cost_optimal_validation[k.toLowerCase()])}</div></div>)}
          <p className="col-span-2 text-[12px] text-ink-500">Confusion matrix at the cost-optimal threshold (test period, transaction level).</p>
        </div>
      </div>
      <div>
        <h2 className="mb-1">Expected cost vs threshold (test period)</h2>
        <EChart ariaLabel="Line chart of expected review plus missed-value cost against alert threshold" height={240}
          option={{ grid: { left: 80, right: 20, top: 10, bottom: 36 }, tooltip: { trigger: 'axis' }, xAxis: { type: 'value', name: 'threshold', nameLocation: 'middle', nameGap: 24, ...axis() }, yAxis: { type: 'value', name: 'expected cost (₹)', ...axis({ axisLabel: { color: '#4b556b', fontSize: 11, formatter: (v: number) => inr(v, true) } }) },
            series: [{ type: 'line', symbol: 'none', data: em.cost_curve_test.map((c: Any) => [c.threshold, c.expected_cost]) }] }} />
        <Notice tone="warn">{em.cost_assumptions.note} Assumed: ₹{em.cost_assumptions.cost_per_false_positive_inr} per false-positive review, {pct(em.cost_assumptions.loss_rate_on_missed_value, 0)} of missed suspicious value. The optimum is specific to these assumptions, not universally correct.</Notice>
      </div>
      <div>
        <h2 className="mb-1">Calibration (selected model, test period)</h2>
        <div className="grid gap-4 md:grid-cols-2">
          {([['Raw model output', cal.raw], ['After isotonic calibration (fit on validation)', cal.isotonic_on_validation]] as [string, Any][]).map(([t, c]) => (
            <div key={t}>
              <EChart ariaLabel={`Reliability diagram: ${t}`} height={230}
                option={{ title: { text: t, textStyle: { fontSize: 12, color: '#363f53' } }, grid: { left: 50, right: 10, top: 34, bottom: 38 }, tooltip: { trigger: 'item' },
                  xAxis: { type: 'value', min: 0, max: 1, name: 'mean predicted', nameLocation: 'middle', nameGap: 24, ...axis() }, yAxis: { type: 'value', min: 0, max: 1, name: 'observed rate', ...axis() },
                  series: [{ type: 'line', data: [[0, 0], [1, 1]], symbol: 'none', lineStyle: { type: 'dashed', color: '#b8bfcc' } }, { type: 'line', data: c.bins.map((b: Any) => [b.mean_predicted, b.observed_rate]), symbolSize: 8 }] }} />
              <p className="text-[12px] text-ink-600">Brier {c.brier.toFixed(4)} · ECE {c.ece.toFixed(4)} · base rate {pct(c.base_rate, 2)}</p>
            </div>
          ))}
        </div>
        <Notice>{cal.conclusion}</Notice>
      </div>
    </div>
  )
}

function Monitoring({ datasetId }: { datasetId?: string }) {
  const { data, loading, error } = useAsync(() => api.get<Any>('/monitoring/summary', { dataset_id: datasetId }), [datasetId], !!datasetId)
  if (loading) return <Spinner />
  if (error) return <ErrorBox message={error} />
  if (!data || data.runs.length === 0) return <Empty title="No pipeline runs yet" hint="Run the analysis pipeline from Data Management." />
  const r = data.runs[0]
  const m = r.metrics
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[['Predictions (latest run)', num(m.n_scored)], ['Failed predictions', num(m.n_failed)], ['Alerts generated', num(m.n_alerts)], ['Scoring latency', `${m.scoring_latency_ms_per_txn} ms/txn`], ['Alert backlog', num(data.alert_backlog)]].map(([a, b]) => <div key={a} className="panel px-4 py-3"><div className="label">{a}</div><div className="tabular text-lg font-semibold">{b}</div></div>)}
      </div>
      <p className="text-ink-600">Model {m.model_version} · feature version {m.feature_version} · risk config {m.risk_config_version} · total run {m.latency_seconds_total}s · stages: {Object.entries(m.stage_seconds).map(([k, v]) => `${k} ${v}s`).join(', ')}</p>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <h2 className="mb-1">Score distribution (latest window)</h2>
          <EChart ariaLabel="Histogram of model risk scores in the latest run" height={200}
            option={{ grid: { left: 50, right: 10, top: 10, bottom: 28 }, tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: m.score_histogram.map((_: number, i: number) => `${(i / 10).toFixed(1)}`), ...axis() }, yAxis: { type: 'log', ...axis() }, series: [{ type: 'bar', data: m.score_histogram.map((v: number) => Math.max(v, 0.5)) }] }} />
        </div>
        <div>
          <h2 className="mb-1">Input drift vs pre-window baseline (PSI)</h2>
          {m.drift ? (
            <>
              <table className="w-full text-[12.5px]" aria-label="Drift"><thead><tr><th className="th">Feature</th><th className="th text-right">PSI</th><th className="th">Level</th></tr></thead>
                <tbody>{[{ feature: 'MODEL SCORE', psi: m.drift.score_psi, level: m.drift.score_level }, ...m.drift.features].map((f: Any) => <tr key={f.feature}><td className="td">{f.feature}</td><td className="td tabular text-right">{f.psi ?? '—'}</td><td className="td">{f.level}</td></tr>)}</tbody></table>
              <p className="mt-1 text-[12px] text-ink-500">{m.drift.caveat}</p>
            </>
          ) : <Empty title="No drift data" />}
        </div>
      </div>
      <Notice>{data.notes.join(' ')}</Notice>
      <div>
        <h2 className="mb-1">Recent pipeline runs</h2>
        <table className="w-full text-[12.5px]" aria-label="Pipeline runs"><thead><tr><th className="th">Run</th><th className="th">Status</th><th className="th">Window</th><th className="th text-right">Scored</th><th className="th text-right">Alerts</th><th className="th text-right">Seconds</th></tr></thead>
          <tbody>{data.runs.map((x: Any) => <tr key={x.run_id}><td className="td font-mono">{x.run_id}</td><td className="td">{x.status}</td><td className="td">{d0(x.window_start)} → {d0(x.window_end)}</td><td className="td tabular text-right">{num(x.metrics?.n_scored)}</td><td className="td tabular text-right">{num(x.metrics?.n_alerts)}</td><td className="td tabular text-right">{x.metrics?.latency_seconds_total}</td></tr>)}</tbody></table>
      </div>
    </div>
  )
}

function Impact() {
  const { data, loading, error, reload } = useAsync(() => api.get<Any>('/evaluation/business-impact'), [])
  if (loading) return <Spinner />
  if (error) return (
    <Empty title="Benchmark not generated yet" hint="Compares simple rules, transaction-only ML, behavioural ML, graph-enhanced ML and AEGIS combined prioritisation at fixed investigation capacity (about 1–2 minutes)."
      action={<JobButton label="Run business-impact benchmark" path="/evaluation/business-impact" onDone={reload} />} />
  )
  const ap: Record<string, Any> = data.approaches
  const names: Record<string, string> = { '1_simple_rules': 'Simple rules', '2_transaction_only_ml': 'Transaction-only ML', '3_behavioral_ml': 'Behavioural ML', '4_graph_enhanced_ml': 'Graph-enhanced ML', '4b_combined_features_ml': 'All features ML', '5_aegis_combined_risk': 'AEGIS combined risk', '5b_pattern_only': 'Pattern evidence only', '5c_model_signal_only': 'Model signal only', '5d_rules_pattern_plus_behavior': 'Rules (pattern + behaviour)' }
  const caps = ['25', '50', '100', '200']
  return (
    <div className="space-y-4">
      <Notice tone="warn"><b>{data.kind}.</b> Evaluation window {d0(data.evaluation_window.start)} → {d0(data.evaluation_window.end)}: {num(data.evaluation_window.n_suspicious_scenarios_in_window)} simulated suspicious scenarios, {num(data.evaluation_window.n_suspicious_transactions)} suspicious transactions among {num(data.evaluation_window.n_transactions)}. Costs are assumptions ({data.assumptions.minutes_per_case} min/case, ₹{data.assumptions.cost_per_case_inr}/case); no savings are claimed.</Notice>
      <div>
        <h2 className="mb-1">Scenario recall at fixed investigation capacity (cases reviewed)</h2>
        <EChart ariaLabel="Line chart of scenario recall at different review capacities by approach" height={300}
          option={{ grid: { left: 50, right: 10, top: 40, bottom: 30 }, legend: { top: 0, type: 'scroll', textStyle: { color: '#4b556b' } }, tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: caps.map((c) => `${c} cases`), ...axis() }, yAxis: { type: 'value', min: 0, max: 1, ...axis() },
            series: Object.keys(ap).filter((k) => !k.startsWith('5b') && !k.startsWith('5c') && !k.startsWith('5d') && !k.startsWith('4b')).map((k) => ({ name: names[k] || k, type: 'line', symbolSize: 7, data: caps.map((c) => +ap[k].by_capacity[c].scenario_recall.toFixed(3)) })) }} />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]" aria-label="Business impact comparison">
          <thead><tr><th className="th">Approach</th>{caps.map((c) => <th key={c} className="th text-right" colSpan={2}>{c} cases</th>)}<th className="th text-right">Cost / detected scenario @100 (assumed)</th></tr>
            <tr><th className="th" />{caps.flatMap((c) => [<th key={c + 'p'} className="th text-right">Precision</th>, <th key={c + 'r'} className="th text-right">Scen. recall</th>])}<th className="th" /></tr></thead>
          <tbody>{Object.entries(ap).map(([k, v]) => <tr key={k} className={k.startsWith('5_') ? 'bg-brand-50 font-medium' : ''}><td className="td">{names[k] || k}<div className="text-[11px] font-normal text-ink-500">{v.description}</div></td>
            {caps.flatMap((c) => [<td key={c + 'p'} className="td tabular text-right">{pct(v.by_capacity[c].precision, 0)}</td>, <td key={c + 'r'} className="td tabular text-right">{pct(v.by_capacity[c].scenario_recall, 0)}</td>])}
            <td className="td tabular text-right">{inr(v.by_capacity['100'].assumed_cost_per_detected_scenario_inr)}</td></tr>)}</tbody></table>
      </div>
      {data.robustness && (
        <div>
          <h2 className="mb-1">Evasion stress test</h2>
          <p className="mb-1 text-[12px] text-ink-500">{data.robustness.description}</p>
          <table className="w-full text-[12.5px]" aria-label="Robustness"><thead><tr><th className="th">Evasion level</th><th className="th text-right">Model PR-AUC</th><th className="th text-right">Model recall @1%</th><th className="th text-right">Rules PR-AUC</th><th className="th text-right">Rules recall @1%</th></tr></thead>
            <tbody>{data.robustness.rows.map((r: Any) => <tr key={r.evasion_level}><td className="td">{r.evasion_level}</td><td className="td tabular text-right">{f3(r.model_pr_auc)}</td><td className="td tabular text-right">{pct(r.model_recall_at_1pct)}</td><td className="td tabular text-right">{f3(r.rules_pr_auc)}</td><td className="td tabular text-right">{pct(r.rules_recall_at_1pct)}</td></tr>)}</tbody></table>
        </div>
      )}
      <ul className="list-disc space-y-1 pl-5 text-ink-600">{data.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
      <JobButton label="Re-run benchmark" path="/evaluation/business-impact" onDone={reload} />
    </div>
  )
}
