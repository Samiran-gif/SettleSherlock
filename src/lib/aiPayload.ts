// Builds the minimal payload sent to the model, plus the cache key.
//
// Two cost rules are enforced here:
//   1. Only the records needed to explain THIS transaction are sent -- never
//      the corpus, never the raw CSVs.
//   2. The cache hash excludes wall-clock time. The demo clock shifts on every
//      page load, so hashing timestamps would miss the cache on every reload
//      and re-bill for an identical question.

import type { TracedTransaction } from '../types'

export interface AnalysisPayload {
  id: string
  status: string
  stoppedAt: string | null
  signature: string
  evidenceStrength: string
  definitiveFailureCode: string | null
  /** Coarse bucket, not an exact duration -- keeps the cache key stable. */
  pendingBucket: string
  amountPaise: number
  money: {
    customerCharged: boolean
    bankAuthorized: boolean
    settlementCompleted: boolean
    ledgerUpdated: boolean
    reversed: boolean
  }
  /** Only the fields the model needs to cite evidence correctly. */
  evidence: {
    id: string
    system: string
    kind: string
    event: string
    code: string | null
    detail: string
    /** Seconds since the first event -- relative, so it is clock-independent. */
    tOffsetSec: number | null
  }[]
  cohort: { total: number; settled: number; reversed: number; failed: number }
}

function pendingBucket(ms: number | null): string {
  if (ms === null) return 'TERMINAL'
  if (ms < 5 * 60_000) return 'UNDER_5_MIN'
  if (ms < 30 * 60_000) return 'UNDER_30_MIN'
  if (ms < 6 * 3600_000) return 'UNDER_6_HOURS'
  return 'OVER_6_HOURS'
}

export function buildPayload(
  txn: TracedTransaction,
  cohort: { total: number; settled: number; reversed: number; failed: number },
): AnalysisPayload {
  const times = txn.evidence
    .map((e) => (e.timestamp ? Date.parse(e.timestamp) : null))
    .filter((t): t is number => t !== null)
  const t0 = times.length > 0 ? Math.min(...times) : null

  return {
    id: txn.record.id,
    status: txn.status,
    stoppedAt: txn.stoppedAt,
    signature: txn.signature,
    evidenceStrength: txn.evidenceStrength,
    definitiveFailureCode: txn.definitiveFailureCode,
    pendingBucket: pendingBucket(txn.pendingForMs),
    amountPaise: txn.record.amountPaise,
    money: {
      customerCharged: txn.money.customerCharged,
      bankAuthorized: txn.money.bankAuthorized,
      settlementCompleted: txn.money.settlementCompleted,
      ledgerUpdated: txn.money.ledgerUpdated,
      reversed: txn.money.reversed,
    },
    evidence: txn.evidence.map((e) => ({
      id: e.id,
      system: e.system,
      kind: e.kind,
      event: e.event,
      code: e.code,
      detail: e.detail,
      tOffsetSec:
        e.timestamp && t0 !== null ? Math.round((Date.parse(e.timestamp) - t0) / 1000) : null,
    })),
    cohort,
  }
}

/** Deterministic 32-bit hash (FNV-1a) over canonical JSON. No crypto needed. */
function hash32(s: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(36)
}

/**
 * Cache key input. Deliberately omits absolute timestamps and the cohort
 * counts (which can shift with the clock window) so the same transaction
 * always resolves to the same key.
 */
export function payloadHash(p: AnalysisPayload): string {
  const canonical = JSON.stringify([
    p.id,
    p.status,
    p.stoppedAt,
    p.signature,
    p.evidenceStrength,
    p.definitiveFailureCode,
    p.pendingBucket,
    p.money,
    p.evidence.map((e) => [e.id, e.event, e.code, e.kind]),
  ])
  return hash32(canonical)
}
