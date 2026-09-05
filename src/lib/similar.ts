// Cohort statistics for "have we seen this before?".
//
// Entirely local arithmetic over the mock dataset -- the LLM is never asked to
// count or average anything.

import { allTransactionIds } from './dataset'
import { traceTransaction } from './trace'
import type { SimilarPeer, SimilarSummary, TracedTransaction, TxnStatus } from '../types'

/**
 * Tracing every transaction is cheap but not free, and the signature index is
 * reused by both the similar-transactions panel and system health. Built once.
 */
let cache: { now: number; traces: TracedTransaction[] } | null = null

function allTraces(now: number): TracedTransaction[] {
  // The demo clock offset is fixed per page load, so a single build is stable.
  if (cache) return cache.traces
  const traces: TracedTransaction[] = []
  for (const id of allTransactionIds()) {
    const t = traceTransaction(id, now)
    if (t) traces.push(t)
  }
  cache = { now, traces }
  return traces
}

function resolutionMs(t: TracedTransaction): number | null {
  const terminal = t.status === 'SETTLED' || t.status === 'FAILED' || t.status === 'REVERSED'
  if (!terminal || !t.lastEventAt) return null
  const start = Date.parse(t.record.createdAt)
  const end = Date.parse(t.lastEventAt)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null
  return end - start
}

/** Peers sharing the subject's incident signature, with outcome statistics. */
export function findSimilar(subject: TracedTransaction, now = Date.now(), limit = 8): SimilarSummary {
  const peers: SimilarPeer[] = []
  let settled = 0
  let reversed = 0
  let failed = 0
  let unresolved = 0
  let resolutionTotal = 0
  let resolutionCount = 0

  for (const t of allTraces(now)) {
    if (t.record.id === subject.record.id) continue
    if (t.signature !== subject.signature) continue

    const outcome: TxnStatus = t.status
    if (outcome === 'SETTLED') settled++
    else if (outcome === 'REVERSED') reversed++
    else if (outcome === 'FAILED') failed++
    else unresolved++

    const res = resolutionMs(t)
    if (res !== null) {
      resolutionTotal += res
      resolutionCount++
    }

    peers.push({
      id: t.record.id,
      merchantName: t.record.merchantName,
      amountPaise: t.record.amountPaise,
      outcome,
      createdAt: t.record.createdAt,
      resolutionMs: res,
    })
  }

  // Most recent first -- the useful examples for a support agent.
  peers.sort((a, b) => b.createdAt.localeCompare(a.createdAt))

  return {
    signature: subject.signature,
    total: peers.length,
    settled,
    reversed,
    failed,
    unresolved,
    avgResolutionMs: resolutionCount > 0 ? Math.round(resolutionTotal / resolutionCount) : null,
    peers: peers.slice(0, limit),
  }
}

/** Full peer list, for the "view similar transactions" drill-down. */
export function findSimilarPeers(subject: TracedTransaction, now = Date.now()): SimilarPeer[] {
  return findSimilar(subject, now, Number.MAX_SAFE_INTEGER).peers
}

/** Exposed for system health, which needs the same corpus. */
export function corpus(now = Date.now()): TracedTransaction[] {
  return allTraces(now)
}
