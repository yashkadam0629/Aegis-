import { ReactNode, useEffect } from 'react'
import { BAND_COLOR } from '../utils/format'

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 p-6 text-ink-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink-300 border-t-brand-600" />
      {label}
    </div>
  )
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="m-4 rounded border border-red-200 bg-red-50 p-3 text-risk-critical">
      <div className="font-medium">Something went wrong</div>
      <div className="mt-0.5 text-[12px]">{message}</div>
      {onRetry && (
        <button className="btn mt-2" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 p-10 text-center">
      <div className="text-sm font-medium text-ink-700">{title}</div>
      {hint && <div className="max-w-md text-ink-500">{hint}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function Panel({ title, right, children, className = '' }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between border-b border-ink-100 px-4 py-2.5">
          <h2>{title}</h2>
          <div className="flex items-center gap-2">{right}</div>
        </header>
      )}
      {children}
    </section>
  )
}

export function Kpi({ label, value, hint, def }: { label: string; value: ReactNode; hint?: ReactNode; def?: string }) {
  return (
    <div className="panel px-4 py-3" title={def}>
      <div className="flex items-center gap-1 text-[11px] font-medium uppercase tracking-wide text-ink-500">
        {label}
        {def && (
          <span aria-label={`Definition: ${def}`} className="cursor-help rounded-full border border-ink-300 px-1 text-[9px] normal-case text-ink-400">
            i
          </span>
        )}
      </div>
      <div className="tabular mt-1 text-[22px] font-semibold leading-tight text-ink-900">{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-ink-500">{hint}</div>}
    </div>
  )
}

export function BandBadge({ band }: { band: string }) {
  const c = BAND_COLOR[band] || '#657089'
  return (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold" style={{ color: c, background: c + '14', border: `1px solid ${c}33` }}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: c }} />
      {band}
    </span>
  )
}

const STATUS_STYLE: Record<string, string> = {
  'Not opened': 'bg-ink-100 text-ink-600',
  New: 'bg-blue-50 text-blue-700',
  'In Review': 'bg-amber-50 text-amber-800',
  Escalated: 'bg-red-50 text-red-700',
  Resolved: 'bg-green-50 text-green-800',
  Closed: 'bg-ink-100 text-ink-600',
}
export function StatusBadge({ status }: { status: string }) {
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${STATUS_STYLE[status] || 'bg-ink-100 text-ink-700'}`}>{status}</span>
}

export function Tag({ children }: { children: ReactNode }) {
  return <span className="rounded border border-ink-200 bg-ink-50 px-1.5 py-0.5 text-[11px] text-ink-700">{children}</span>
}

export function Drawer({ open, onClose, title, children, width = 640 }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: number }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    if (open) window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-ink-900/30" onClick={onClose} aria-hidden />
      <aside role="dialog" aria-modal="true" aria-label="Details" className="relative z-10 flex h-full flex-col bg-white shadow-xl" style={{ width: `min(${width}px, 100vw)` }}>
        <header className="flex items-start justify-between border-b border-ink-200 px-5 py-3">
          <div className="min-w-0 flex-1">{title}</div>
          <button className="btn ml-3" onClick={onClose} aria-label="Close details">
            Close
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  )
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: string }[]; value: string; onChange: (id: string) => void }) {
  return (
    <div role="tablist" className="flex gap-1 border-b border-ink-200 px-4">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-[13px] font-medium ${value === t.id ? 'border-brand-600 text-brand-700' : 'border-transparent text-ink-500 hover:text-ink-800'}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <div className="flex items-center justify-between border-t border-ink-100 px-4 py-2 text-ink-600">
      <span className="tabular">
        {total === 0 ? '0 results' : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total.toLocaleString('en-IN')}`}
      </span>
      <div className="flex items-center gap-2">
        <button className="btn" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </button>
        <span className="tabular">
          Page {page} / {pages}
        </span>
        <button className="btn" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </button>
      </div>
    </div>
  )
}

export function Notice({ children, tone = 'info' }: { children: ReactNode; tone?: 'info' | 'warn' }) {
  const cls = tone === 'warn' ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-blue-100 bg-brand-50 text-ink-700'
  return <div className={`rounded border px-3 py-2 text-[12px] ${cls}`}>{children}</div>
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
    </label>
  )
}
