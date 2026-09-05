// "Have we seen this before?"
//
// Every number here is computed from the mock dataset by lib/similar.ts. The
// model is never asked to count or average anything.

import { forwardRef, useState } from 'react'
import { formatCurrency, formatDateTime, formatDuration } from '../lib/format'
import { StatusBadge } from './primitives'
import type { SimilarSummary } from '../types'

function Stat({ value, label, tone = 'text-ink' }: { value: string; label: string; tone?: string }) {
  return (
    <div>
      <div className={`tnum text-xl font-semibold ${tone}`}>{value}</div>
      <div className="text-2xs uppercase tracking-wide text-muted">{label}</div>
    </div>
  )
}

export const SimilarTransactions = forwardRef<
  HTMLElement,
  { summary: SimilarSummary; expanded: boolean; onToggle: () => void }
>(function SimilarTransactions({ summary, expanded, onToggle }, ref) {
  const [showAll, setShowAll] = useState(false)
  const peers = showAll ? summary.peers : summary.peers.slice(0, 5)

  return (
    <section ref={ref} className="card scroll-mt-4" aria-labelledby="similar-title">
      <div className="card-header">
        <h2 id="similar-title" className="section-title">
          Have we seen this before?
        </h2>
        <span className="chip" title="Grouped by the same incident signature">
          {summary.signature}
        </span>
      </div>

      <div className="px-4 py-4">
        {summary.total === 0 ? (
          <p className="text-sm text-ink-2">
            No other transaction in the demo dataset shares this incident pattern.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
              <Stat value={String(summary.total)} label="similar transactions" />
              {summary.settled > 0 && (
                <Stat value={String(summary.settled)} label="eventually settled" tone="text-success" />
              )}
              {summary.reversed > 0 && (
                <Stat value={String(summary.reversed)} label="were reversed" tone="text-info" />
              )}
              {summary.failed > 0 && (
                <Stat value={String(summary.failed)} label="failed" tone="text-failure" />
              )}
              {summary.avgResolutionMs !== null && (
                <Stat value={formatDuration(summary.avgResolutionMs)} label="avg resolution" />
              )}
            </div>

            <button type="button" onClick={onToggle} className="btn mt-4" aria-expanded={expanded}>
              {expanded ? 'Hide similar transactions' : 'View similar transactions'}
            </button>

            {expanded && (
              <div className="fade-rise mt-3 overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-line text-2xs uppercase tracking-wide text-muted">
                      <th scope="col" className="py-2 pr-3 font-medium">Transaction</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Merchant</th>
                      <th scope="col" className="py-2 pr-3 text-right font-medium">Amount</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Outcome</th>
                      <th scope="col" className="py-2 pr-3 text-right font-medium">Resolved in</th>
                      <th scope="col" className="py-2 font-medium">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {peers.map((p) => (
                      <tr key={p.id} className="border-b border-line/60 last:border-b-0">
                        <td className="py-2 pr-3 font-mono text-2xs text-ink">{p.id}</td>
                        <td className="py-2 pr-3 text-ink-2">{p.merchantName}</td>
                        <td className="tnum py-2 pr-3 text-right text-ink-2">
                          {formatCurrency(p.amountPaise)}
                        </td>
                        <td className="py-2 pr-3">
                          <StatusBadge status={p.outcome} />
                        </td>
                        <td className="tnum py-2 pr-3 text-right text-ink-2">
                          {p.resolutionMs !== null ? formatDuration(p.resolutionMs) : '—'}
                        </td>
                        <td className="tnum py-2 text-muted">{formatDateTime(p.createdAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {summary.peers.length > 5 && (
                  <button type="button" onClick={() => setShowAll((v) => !v)} className="btn mt-3">
                    {showAll ? 'Show fewer' : `Show all ${summary.peers.length}`}
                  </button>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  )
})
