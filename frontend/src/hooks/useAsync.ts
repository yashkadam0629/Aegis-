import { DependencyList, useCallback, useEffect, useRef, useState } from 'react'

export interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
}

/** Runs `fn` whenever deps change; ignores stale responses. */
export function useAsync<T>(fn: () => Promise<T>, deps: DependencyList, enabled = true): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const seq = useRef(0)
  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return
    }
    const id = ++seq.current
    setLoading(true)
    setError(null)
    fn()
      .then((d) => {
        if (id === seq.current) {
          setData(d)
          setLoading(false)
        }
      })
      .catch((e: Error) => {
        if (id === seq.current) {
          setError(e.message || 'Request failed')
          setLoading(false)
        }
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled])
  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, loading, error, reload }
}
