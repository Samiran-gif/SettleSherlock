// Presentation formatters. Pure, synchronous, no AI -- these are exactly the
// kind of computation that must never cost an API call.

/** Amounts are stored as integer paise to avoid float drift. */
export function formatCurrency(amountPaise: number, currency = 'INR'): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    maximumFractionDigits: amountPaise % 100 === 0 ? 0 : 2,
  }).format(amountPaise / 100)
}

/** Compact duration: "3m 42s", "2h 14m", "6s". */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '--'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) {
    const rem = s % 60
    return rem === 0 ? `${m}m` : `${m}m ${rem}s`
  }
  const h = Math.floor(m / 60)
  if (h < 24) {
    const rem = m % 60
    return rem === 0 ? `${h}h` : `${h}h ${rem}m`
  }
  const d = Math.floor(h / 24)
  const rem = h % 24
  return rem === 0 ? `${d}d` : `${d}d ${rem}h`
}

/** Wall-clock time of day, e.g. "10:42:47". */
export function formatTime(iso: string | null): string {
  if (!iso) return '--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--'
  return d.toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** Date + time, for records older than today. */
export function formatDateTime(iso: string | null): string {
  if (!iso) return '--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--'
  const today = new Date()
  const sameDay = d.toDateString() === today.toDateString()
  if (sameDay) return formatTime(iso)
  return `${d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })} ${formatTime(iso)}`
}

/** "4m ago" / "just now". */
export function formatAgo(iso: string | null, now = Date.now()): string {
  if (!iso) return '--'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '--'
  const delta = now - t
  if (delta < 5000) return 'just now'
  return `${formatDuration(delta)} ago`
}

/** Turn an event name into a stable kebab id fragment: NO_RESPONSE -> no-response */
export function kebab(s: string): string {
  return s.toLowerCase().replace(/_/g, '-')
}
