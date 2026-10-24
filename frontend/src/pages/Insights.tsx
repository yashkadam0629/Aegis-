import { Empty, ErrorBox, Notice, Panel, Spinner } from '../components/ui'
import { useDataset } from '../hooks/DatasetContext'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'

interface Item { title: string; text: string; evidence: unknown }

export default function Insights() {
  const { datasetId } = useDataset()
  const { data, loading, error, reload } = useAsync(() => api.get<{ items: Item[]; method: string }>('/insights', { dataset_id: datasetId }), [datasetId], !!datasetId)
  if (loading && !data) return <Spinner />
  if (error) return <ErrorBox message={error} onRetry={reload} />
  if (!data || data.items.length === 0) return <Empty title="No insights yet" hint="Run the analysis pipeline to generate alerts." />
  return (
    <div className="space-y-4">
      <div>
        <h1>Executive Insights</h1>
        <p className="text-ink-500">Summaries are generated deterministically from database aggregates. The evidence behind each statement is shown beneath it.</p>
      </div>
      <Notice>Method: {data.method}.</Notice>
      <div className="grid gap-4 lg:grid-cols-2">
        {data.items.map((it) => (
          <Panel key={it.title} title={it.title}>
            <div className="space-y-2 p-4">
              <p className="text-[14px] text-ink-800">{it.text}</p>
              <details className="text-[12px] text-ink-600">
                <summary className="cursor-pointer text-brand-600">Supporting evidence</summary>
                <pre className="mt-2 max-h-52 overflow-auto rounded bg-ink-50 p-2 font-mono text-[11px]">{JSON.stringify(it.evidence, null, 2)}</pre>
              </details>
            </div>
          </Panel>
        ))}
      </div>
    </div>
  )
}
