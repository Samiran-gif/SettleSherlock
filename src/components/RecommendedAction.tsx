// "What should I do?" -- the resolution assistant.
//
// The buttons perform real work inside the demo: monitoring adds the
// transaction to a watchlist, escalation drafts a note built from the actual
// trace, and the similar-incidents action reveals the cohort panel.

import { formatDuration } from '../lib/format'
import type { Diagnosis, TracedTransaction } from '../types'

export function RecommendedAction({
  txn,
  diagnosis,
  monitored,
  onMonitor,
  onEscalate,
  onViewSimilar,
  similarCount,
}: {
  txn: TracedTransaction
  diagnosis: Diagnosis | null
  monitored: boolean
  onMonitor: () => void
  onEscalate: () => void
  onViewSimilar: () => void
  similarCount: number
}) {
  if (!diagnosis) return null
  const a = diagnosis.recommendedAction

  return (
    <section className="card" aria-labelledby="action-title">
      <div className="card-header">
        <h2 id="action-title" className="section-title">
          What should I do?
        </h2>
        {txn.pendingForMs !== null && (
          <span className="tnum chip" title="Time since the last recorded event">
            Pending {formatDuration(txn.pendingForMs)}
          </span>
        )}
      </div>

      <div className="px-4 py-4">
        <div className="rounded-md border border-primary/30 bg-primary/5 px-3.5 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-primary">
            Recommended action
          </div>
          <div className="mt-1 text-base font-semibold leading-snug text-ink">{a.title}</div>
          <p className="mt-1.5 text-xs leading-relaxed text-ink-2">{a.reason}</p>

          <div className="mt-2.5">
            {a.retryRecommended ? (
              <span className="chip border-success/40 text-success">
                <span aria-hidden="true">✓</span> Retry is safe
              </span>
            ) : (
              <span className="chip border-warning/40 text-warning">
                <span aria-hidden="true">⚠</span> Do not retry yet
              </span>
            )}
          </div>
        </div>

        <div className="mt-3.5 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onMonitor}
            className={`btn ${monitored ? 'border-success/50 text-success' : ''}`}
            aria-pressed={monitored}
          >
            {monitored ? '✓ Monitoring' : 'Monitor transaction'}
          </button>
          <button type="button" onClick={onEscalate} className="btn">
            Escalate to bank
          </button>
          <button type="button" onClick={onViewSimilar} className="btn" disabled={similarCount === 0}>
            View similar incidents{similarCount > 0 ? ` (${similarCount})` : ''}
          </button>
        </div>
      </div>
    </section>
  )
}

/**
 * Escalation note drafted entirely from the trace -- no AI call. Shown in a
 * dialog so the agent can copy it into a real ticketing system.
 */
export function buildEscalationNote(txn: TracedTransaction, diagnosis: Diagnosis | null): string {
  const lines: string[] = []
  lines.push(`Settlement escalation — ${txn.record.id}`)
  lines.push(`Merchant: ${txn.record.merchantName} (${txn.record.merchantId})`)
  lines.push(`Method: ${txn.record.method}`)
  lines.push(`Status: ${txn.status}${txn.stoppedAt ? ` (stopped at ${txn.stoppedAt})` : ''}`)
  if (txn.definitiveFailureCode) lines.push(`Reported code: ${txn.definitiveFailureCode}`)
  if (diagnosis?.likelyCause) {
    lines.push(`Assessed cause: ${diagnosis.likelyCause.text} (${diagnosis.likelyCause.confidence}% confidence)`)
  } else {
    lines.push('Assessed cause: not determined — evidence incomplete')
  }
  lines.push('')
  lines.push('Observed events:')
  for (const e of txn.evidence) {
    const at = e.timestamp ? new Date(e.timestamp).toISOString() : '—'
    lines.push(`  [${e.system}] ${at}  ${e.event}${e.code ? ` (${e.code})` : ''}`)
  }
  lines.push('')
  lines.push('Money state:')
  lines.push(`  customer charged: ${txn.money.customerCharged}`)
  lines.push(`  bank authorized: ${txn.money.bankAuthorized}`)
  lines.push(`  settlement completed: ${txn.money.settlementCompleted}`)
  lines.push(`  ledger updated: ${txn.money.ledgerUpdated}`)
  lines.push('')
  lines.push('Generated from demo (mock) transaction data.')
  return lines.join('\n')
}
