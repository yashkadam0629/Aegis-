export const inr = (v: number | null | undefined, compact = false): string => {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  if (compact) {
    const a = Math.abs(v)
    if (a >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`
    if (a >= 1e5) return `₹${(v / 1e5).toFixed(2)} L`
    if (a >= 1e3) return `₹${(v / 1e3).toFixed(1)}K`
  }
  return '₹' + v.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}
export const num = (v: number | null | undefined): string => (v === null || v === undefined ? '—' : v.toLocaleString('en-IN'))
export const pct = (v: number | null | undefined, d = 1): string => (v === null || v === undefined || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(d)}%`)
export const f3 = (v: number | null | undefined): string => (v === null || v === undefined || Number.isNaN(v) ? '—' : v.toFixed(3))
export const dt = (s: string | null | undefined): string => (s ? new Date(s.endsWith('Z') ? s : s + 'Z').toLocaleString('en-GB', { timeZone: 'UTC', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) + ' UTC' : '—')
export const d0 = (s: string | null | undefined): string => (s ? s.slice(0, 10) : '—')
export const label = (s: string): string => s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export const BAND_COLOR: Record<string, string> = { Critical: '#b42318', High: '#c2570c', Medium: '#a16207', Low: '#3f7d58', Minimal: '#657089' }
