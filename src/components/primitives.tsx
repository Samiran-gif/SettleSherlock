// Small shared presentational pieces. Every status carries a glyph and a text
// label as well as a colour, so nothing depends on colour perception alone.

import type { EvidenceTone, HealthState, NodeState, TxnStatus } from '../types'

// ---------------------------------------------------------------------------
// Node state styling
// ---------------------------------------------------------------------------

export const NODE_STATE_LABEL: Record<NodeState, string> = {
  SUCCESS: 'Completed',
  PROCESSING: 'Processing',
  PENDING: 'Waiting',
  FAILED: 'Failed',
  UNKNOWN: 'No data',
}

interface StateStyle {
  ring: string
  text: string
  dot: string
  border: string
}

export const NODE_STATE_STYLE: Record<NodeState, StateStyle> = {
  SUCCESS: { ring: 'border-success/60 bg-success/10', text: 'text-success', dot: 'bg-success', border: 'border-success/60' },
  PROCESSING: { ring: 'border-info/60 bg-info/10', text: 'text-info', dot: 'bg-info', border: 'border-info/60' },
  PENDING: { ring: 'border-warning/60 bg-warning/10', text: 'text-warning', dot: 'bg-warning', border: 'border-warning/60' },
  FAILED: { ring: 'border-failure/60 bg-failure/10', text: 'text-failure', dot: 'bg-failure', border: 'border-failure/60' },
  UNKNOWN: { ring: 'border-line-strong border-dashed bg-bg', text: 'text-muted', dot: 'bg-muted', border: 'border-line-strong' },
}

/** Glyph for a node state. Decorative only -- callers supply the text label. */
export function StateIcon({ state, className = 'h-5 w-5' }: { state: NodeState; className?: string }) {
  const common = { className, viewBox: '0 0 24 24', fill: 'none', 'aria-hidden': true as const }
  switch (state) {
    case 'SUCCESS':
      return (
        <svg {...common}>
          <path
            className="check-draw"
            d="M5 13l4 4L19 7"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )
    case 'FAILED':
      return (
        <svg {...common}>
          <path d="M7 7l10 10M17 7L7 17" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
      )
    case 'PENDING':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="2" />
          <path d="M12 8v4l2.5 2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      )
    case 'PROCESSING':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="2" strokeOpacity="0.28" />
          <path className="state-processing-ring origin-center" d="M12 4a8 8 0 018 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      )
    case 'UNKNOWN':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="2" strokeDasharray="3 3" />
          <path d="M12 15.5v.01M12 8a2 2 0 011.7 3.05c-.4.6-1.2.95-1.4 1.55" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      )
  }
}

// ---------------------------------------------------------------------------
// Evidence tone glyph -- the check / warning / cross / open-circle marks
// ---------------------------------------------------------------------------

const TONE: Record<EvidenceTone, { mark: string; cls: string; sr: string }> = {
  ok: { mark: '✓', cls: 'text-success', sr: 'Confirmed' },
  warn: { mark: '⚠', cls: 'text-warning', sr: 'Warning' },
  bad: { mark: '✕', cls: 'text-failure', sr: 'Failed' },
  neutral: { mark: '○', cls: 'text-muted', sr: 'Not observed' },
}

export function ToneGlyph({ tone }: { tone: EvidenceTone }) {
  const t = TONE[tone]
  return (
    <span className={`select-none font-semibold leading-none ${t.cls}`}>
      <span aria-hidden="true">{t.mark}</span>
      <span className="sr-only">{t.sr}: </span>
    </span>
  )
}

// ---------------------------------------------------------------------------
// Overall transaction status badge
// ---------------------------------------------------------------------------

const STATUS_STYLE: Record<TxnStatus, { cls: string; label: string }> = {
  SETTLED: { cls: 'border-success/40 bg-success/10 text-success', label: 'Settled' },
  PENDING: { cls: 'border-warning/40 bg-warning/10 text-warning', label: 'Pending' },
  FAILED: { cls: 'border-failure/40 bg-failure/10 text-failure', label: 'Failed' },
  REVERSED: { cls: 'border-info/40 bg-info/10 text-info', label: 'Reversed' },
  UNKNOWN: { cls: 'border-line-strong bg-bg text-ink-2', label: 'Indeterminate' },
}

export function StatusBadge({ status, size = 'md' }: { status: TxnStatus; size?: 'md' | 'lg' }) {
  const s = STATUS_STYLE[status]
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full border font-semibold ${s.cls} ${
        size === 'lg' ? 'px-3 py-1 text-sm' : 'px-2 py-0.5 text-xs'
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${STATUS_STYLE[status].cls.includes('text-success') ? 'bg-success' : 'bg-current'}`} aria-hidden="true" />
      {s.label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// System health pill
// ---------------------------------------------------------------------------

export const HEALTH_STYLE: Record<HealthState, { cls: string; label: string; dot: string }> = {
  OPERATIONAL: { cls: 'text-success', label: 'Operational', dot: 'bg-success' },
  DELAYED: { cls: 'text-warning', label: 'Delayed', dot: 'bg-warning' },
  DEGRADED: { cls: 'text-failure', label: 'Degraded', dot: 'bg-failure' },
}

/** Loading placeholder used while an investigation runs. */
export function SkeletonLine({ className = '' }: { className?: string }) {
  return <div className={`shimmer h-3 rounded bg-line/60 ${className}`} aria-hidden="true" />
}
