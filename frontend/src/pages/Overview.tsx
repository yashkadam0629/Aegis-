import { Link } from 'react-router-dom'
import { EChart, axis } from '../components/EChart'
import { Empty, ErrorBox, Kpi, Notice, Panel, Spinner } from '../components/ui'
import { useDataset } from '../hooks/DatasetContext'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { Overview as OV } from '../types'
import { BAND_COLOR, f3, inr, label, num, pct } from '../utils/format'

export default function Overview() {
  const { datasetId, loading: dsLoading } = useDataset()
  const { data, loading, error, reload } = useAsync(() => api.get<OV>('/overview', { dataset_id: datasetId }), [datasetId], !!datasetId)

  if (dsLoading) return <Spinner />
  if (!datasetId) return <Empty title="No dataset loaded" hint="Generate a synthetic dataset or import a CSV to begin." action={<Link className="btn btn-primary" to="/data">Go to Data Management</Link>} />
  if (loading && !data) return <Spinner />
  if (error) return <ErrorBox message={error} onRetry={reload} />
  if (!data) return null
  const k = data.kpis
  const d = data.definitions
  const mp = data.model_performance
  const bands = ['Critical', 'High', 'Medium', 'Low']
  const typeData = [...data.alerts_by_type].sort((a, b) => b.exposed_value - a.exposed_value)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1>Overview</h1>
          <p className="text-ink-500">
            {data.dataset.name} · {data.dataset.kind === 'synthetic' ? 'synthetic data – patterns are simulated' : 'uploaded data'} · version {data.dataset.dataset_version}
          </p>
        </div>
        <Link className="btn" to="/alerts">Open alert center</Link>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-5">
        <Kpi label="Transactions analysed" value={num(k.transactions_analyzed)} />
        <Kpi label="Accounts analysed" value={num(k.accounts_analyzed)} />
        <Kpi label="Active alerts" value={num(k.active_alerts)} hint={`${num(k.alerts_total)} total`} def={d.active_alerts} />
        <Kpi label="High-priority investigations" value={num(k.high_priority_investigations)} hint="open, High or Critical" />
        <Kpi label="Value processed" value={inr(k.total_value_processed, true)} hint="gross, all transactions" />
        <Kpi label="Potentially exposed value" value={inr(k.potentially_exposed_value, true)} def={d.potentially_exposed_value} hint="estimate – not a loss" />
        <Kpi label="Unique value in alerts" value={inr(k.unique_alert_value, true)} def={d.unique_alert_value} hint="de-duplicated across alerts" />
        <Kpi label="Detected network leads" value={num(k.detected_networks)} def={d.detected_networks} />
        <Kpi label="Median open case age" value={k.median_investigation_age_days === null ? '—' : `${k.median_investigation_age_days} d`} hint="open investigations" />
        <Kpi label="Model PR-AUC (test)" value={mp ? f3(mp.pr_auc_test) : '—'} def={d.model_performance} hint={mp ? `rules baseline ${f3(mp.rules_baseline_pr_auc)}` : 'no model trained'} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Alerts by risk band" className="lg:col-span-1">
          <div className="p-3">
            <EChart
              ariaLabel="Bar chart of alert counts by risk band"
              height={220}
              option={{
                grid: { left: 40, right: 10, top: 10, bottom: 28 },
                tooltip: { trigger: 'axis' },
                xAxis: { type: 'category', data: bands, ...axis() },
                yAxis: { type: 'value', ...axis() },
                series: [{ type: 'bar', barWidth: '50%', data: bands.map((b) => ({ value: data.alerts_by_band[b] || 0, itemStyle: { color: BAND_COLOR[b] } })) }],
              }}
            />
            <p className="px-1 text-[11px] text-ink-500">Bands are operational categories of a model-generated score, not regulatory classifications.</p>
          </div>
        </Panel>
        <Panel title="Potentially exposed value by pattern" className="lg:col-span-2">
          <div className="p-3">
            {typeData.length === 0 ? (
              <Empty title="No alerts yet" hint="Run the analysis pipeline from Data Management." />
            ) : (
              <EChart
                ariaLabel="Horizontal bar chart of estimated exposed value by alert type"
                height={220}
                option={{
                  grid: { left: 150, right: 20, top: 10, bottom: 24 },
                  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v: number) => inr(v, true) },
                  xAxis: { type: 'value', ...axis({ axisLabel: { color: '#4b556b', fontSize: 11, formatter: (v: number) => inr(v, true) } }) },
                  yAxis: { type: 'category', inverse: true, data: typeData.map((t) => label(t.alert_type)), ...axis() },
                  series: [{ type: 'bar', barWidth: '55%', data: typeData.map((t) => t.exposed_value), itemStyle: { color: '#2457c5' } }],
                }}
              />
            )}
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Model performance (selected evaluation)">
          {mp ? (
            <div className="space-y-2 p-4">
              <div className="grid grid-cols-3 gap-3">
                <Kpi label="Selected" value={<span className="text-[14px]">{mp.model}</span>} />
                <Kpi label="PR-AUC" value={f3(mp.pr_auc_test)} />
                <Kpi label="Recall @ 1% review" value={pct(mp.recall_at_1pct)} />
              </div>
              <Notice>{mp.evaluation}. Synthetic labels: results measure recovery of simulated patterns, not real-world fraud detection.</Notice>
              <Link to="/models" className="text-brand-600 hover:underline">Full evaluation →</Link>
            </div>
          ) : (
            <Empty title="No model trained" hint="Train a model from Data Management." />
          )}
        </Panel>
        <Panel title="Data quality (import report)">
          <div className="space-y-2 p-4">
            {data.data_quality.rows_received === null ? (
              <Empty title="No import report" />
            ) : (
              <>
                <div className="grid grid-cols-3 gap-3">
                  <Kpi label="Received" value={num(data.data_quality.rows_received)} />
                  <Kpi label="Accepted" value={num(data.data_quality.rows_accepted)} />
                  <Kpi label="Rejected" value={num(data.data_quality.rows_rejected)} />
                </div>
                <div className="text-ink-600">
                  Duplicates dropped: {num(data.data_quality.duplicates?.exact_duplicates_dropped ?? 0)} · Rejected by reason:{' '}
                  {Object.entries(data.data_quality.rejected_by_reason || {}).map(([r, c]) => `${label(r)} ${c}`).join(', ') || 'none'}
                </div>
                <Link to="/data" className="text-brand-600 hover:underline">Import history &amp; error reports →</Link>
              </>
            )}
          </div>
        </Panel>
      </div>
    </div>
  )
}
