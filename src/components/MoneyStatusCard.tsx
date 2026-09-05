// Money status.
//
// Every boolean here is derived by the tracing layer from the logs. The AI may
// restate the situation in the summary line, but it never sets these values.

import { formatCurrency } from '../lib/format'
import type { TracedTransaction } from '../types'

function Row({ label, value, hint }: { label: string; value: boolean; hint?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line py-2 last:border-b-0">
      <span className="text-xs text-ink-2">
        {label}
        {hint && <span className="ml-1 text-muted">({hint})</span>}
      </span>
      <span
        className={`inline-flex items-center gap-1.5 text-xs font-semibold ${
          value ? 'text-success' : 'text-ink-2'
        }`}
      >
        <span aria-hidden="true">{value ? '✓' : '—'}</span>
        {value ? 'Yes' : 'No'}
      </span>
    </div>
  )
}

export function MoneyStatusCard({
  txn,
  aiSummary,
}: {
  txn: TracedTransaction
  aiSummary: string | null
}) {
  const m = txn.money
  return (
    <section className="card" aria-labelledby="money-title">
      <div className="card-header">
        <h2 id="money-title" className="section-title">
          Money status
        </h2>
        {m.reversed && <span className="chip border-info/40 text-info">Reversed</span>}
      </div>

      <div className="px-4 py-4">
        <div className="tnum text-3xl font-semibold tracking-tight text-ink">
          {formatCurrency(m.amountPaise, m.currency)}
        </div>
        <p className="mt-0.5 text-xs text-muted">
          {txn.record.method} · {txn.record.merchantName}
        </p>

        <div className="mt-4">
          <Row label="Customer charged?" value={m.customerCharged} hint="funds held at source" />
          <Row label="Bank authorized?" value={m.bankAuthorized} />
          <Row label="Settlement completed?" value={m.settlementCompleted} />
          <Row label="Ledger updated?" value={m.ledgerUpdated} />
        </div>

        <div className="mt-4 rounded-md border border-line bg-bg p-3">
          <p className="text-xs leading-relaxed text-ink-2">{aiSummary ?? m.headline}</p>
        </div>
      </div>
    </section>
  )
}
