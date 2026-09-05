// Raw events, collapsed by default so the AI explanation stays primary.

import { SYSTEM_LABEL, SYSTEM_ORDER } from '../lib/trace'
import { formatDateTime } from '../lib/format'
import { ToneGlyph } from './primitives'
import type { TracedTransaction } from '../types'

export function RawEvents({ txn, open, onToggle }: { txn: TracedTransaction; open: boolean; onToggle: () => void }) {
  const count = txn.evidence.filter((e) => e.kind === 'event').length

  return (
    <section className="card" aria-labelledby="raw-title">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <span className="flex items-center gap-2">
          <h2 id="raw-title" className="section-title">
            Raw events
          </h2>
          <span className="chip">{count} recorded</span>
        </span>
        <svg
          className={`h-4 w-4 shrink-0 text-muted transition-transform ${open ? 'rotate-180' : ''}`}
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
        >
          <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="fade-rise border-t border-line px-4 py-4">
          <div className="grid gap-5 sm:grid-cols-3">
            {SYSTEM_ORDER.map((system) => {
              const node = txn.systems.find((s) => s.system === system)
              if (!node) return null
              return (
                <div key={system}>
                  <div className="flex items-baseline justify-between border-b border-line pb-1.5">
                    <h3 className="text-xs font-semibold text-ink">{SYSTEM_LABEL[system]}</h3>
                    <span className="text-2xs uppercase tracking-wide text-muted">{node.state}</span>
                  </div>
                  <ul className="mt-2 space-y-2">
                    {node.events.map((e) => (
                      <li key={e.id} className="flex items-start gap-2">
                        <ToneGlyph tone={e.tone} />
                        <div className="min-w-0">
                          <div className="tnum font-mono text-2xs text-muted">
                            {e.timestamp ? formatDateTime(e.timestamp) : '—'}
                          </div>
                          <div className="font-mono text-2xs font-semibold text-ink">{e.event}</div>
                          {e.code && <div className="font-mono text-2xs text-warning">{e.code}</div>}
                          <p className="mt-0.5 text-2xs leading-snug text-ink-2">{e.detail || e.label}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
