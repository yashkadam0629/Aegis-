import { useEffect, useRef, useState } from 'react'
import { api } from '../services/api'
import type { Job } from '../types'

/** Starts a background job via POST `path`, polls /jobs/{id}, and reports completion. */
export function JobButton({ label, path, params, onDone, primary = false, disabled = false }: { label: string; path: string; params?: Record<string, string | number | boolean | undefined>; onDone?: (j: Job) => void; primary?: boolean; disabled?: boolean }) {
  const [job, setJob] = useState<Job | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const timer = useRef<number | null>(null)
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])

  const poll = (id: string) => {
    timer.current = window.setTimeout(async () => {
      try {
        const j = await api.get<Job>(`/jobs/${id}`)
        setJob(j)
        if (j.status === 'completed') onDone?.(j)
        else if (j.status === 'failed') setErr(j.error || 'Job failed')
        else poll(id)
      } catch (e) {
        setErr((e as Error).message)
      }
    }, 1500)
  }
  const start = async () => {
    setErr(null); setJob(null)
    try {
      const r = await api.post<{ job_id: string }>(path, undefined, params)
      setJob({ job_id: r.job_id, kind: '', status: 'queued', log: [], result: null, error: null })
      poll(r.job_id)
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  const running = job && (job.status === 'queued' || job.status === 'running')
  return (
    <div className="inline-flex flex-col items-start gap-1">
      <button className={`btn ${primary ? 'btn-primary' : ''}`} onClick={start} disabled={disabled || !!running}>{running ? 'Running…' : label}</button>
      {running && <span className="text-[11px] text-ink-500" role="status">{job?.log[job.log.length - 1] || job?.status}</span>}
      {job?.status === 'completed' && <span className="text-[11px] text-green-800" role="status">Completed</span>}
      {err && <span className="text-[11px] text-risk-critical" role="alert">{err}</span>}
    </div>
  )
}
