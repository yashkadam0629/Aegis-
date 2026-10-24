import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react'
import { api } from '../services/api'
import type { Dataset } from '../types'

interface Ctx {
  datasets: Dataset[]
  dataset: Dataset | null
  datasetId: string | undefined
  setDatasetId: (id: string) => void
  refresh: () => Promise<void>
  loading: boolean
  error: string | null
}

const C = createContext<Ctx>({ datasets: [], dataset: null, datasetId: undefined, setDatasetId: () => {}, refresh: async () => {}, loading: true, error: null })

export function DatasetProvider({ children }: { children: ReactNode }) {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [id, setId] = useState<string | undefined>(() => {
    try {
      return localStorage.getItem('aegis.dataset') || undefined
    } catch {
      return undefined
    }
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const r = await api.get<{ items: Dataset[] }>('/datasets')
      setDatasets(r.items)
      setError(null)
      setId((cur) => (cur && r.items.some((d) => d.dataset_id === cur) ? cur : r.items[0]?.dataset_id))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const setDatasetId = (v: string) => {
    setId(v)
    try {
      localStorage.setItem('aegis.dataset', v)
    } catch {
      /* storage unavailable */
    }
  }
  const dataset = datasets.find((d) => d.dataset_id === id) ?? null
  return <C.Provider value={{ datasets, dataset, datasetId: dataset?.dataset_id, setDatasetId, refresh, loading, error }}>{children}</C.Provider>
}

export const useDataset = () => useContext(C)
