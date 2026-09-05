// Client side of the investigation.
//
// Order of operations is the whole cost-control story:
//   1. Check the cache. A hit costs nothing and returns immediately.
//   2. Otherwise call the server route exactly once, on explicit user action.
//   3. Validate hard. Any invalid field falls back to the rule-based
//      diagnosis rather than showing an ungrounded claim.
//
// Only AI results are cached. Rule-based results are free to recompute, and
// caching them would suppress a real analysis once a key is configured.

import { buildPayload, payloadHash } from './aiPayload'
import { readCache, writeCache } from './cache'
import { ruleBasedDiagnosis } from './rulesDiagnosis'
import type { CausalClaim, Diagnosis, TracedTransaction } from '../types'

export interface AiStatus {
  available: boolean
  reason: string
  model: string
}

export interface InvestigationResult {
  diagnosis: Diagnosis
  fromCache: boolean
  /** Set when the model was attempted but could not be used. */
  aiError: string | null
  usage: { input: number; output: number } | null
}

export async function fetchAiStatus(): Promise<AiStatus> {
  try {
    const res = await fetch('/api/ai-status')
    if (!res.ok) return { available: false, reason: 'Status endpoint unavailable', model: '' }
    return (await res.json()) as AiStatus
  } catch {
    return { available: false, reason: 'Status endpoint unreachable', model: '' }
  }
}

const isStr = (v: unknown): v is string => typeof v === 'string'

function clampConfidence(v: unknown): number | null {
  if (typeof v !== 'number' || !Number.isFinite(v)) return null
  return Math.max(0, Math.min(100, Math.round(v)))
}

/**
 * Keeps only evidence ids that genuinely exist on the trace. This is the
 * guard that makes "the AI cannot invent evidence" true rather than aspirational.
 */
function validateIds(ids: unknown, valid: Set<string>): string[] {
  if (!Array.isArray(ids)) return []
  return ids.filter((id): id is string => isStr(id) && valid.has(id))
}

function validateClaim(raw: unknown, valid: Set<string>): CausalClaim | null {
  if (typeof raw !== 'object' || raw === null) return null
  const o = raw as Record<string, unknown>
  const text = o.text
  const confidence = clampConfidence(o.confidence)
  if (!isStr(text) || text.trim() === '' || confidence === null) return null
  return { text: text.trim(), confidence, evidenceIds: validateIds(o.evidenceIds, valid) }
}

/**
 * Converts the model's flat payload into a Diagnosis, or returns null if any
 * required part is unusable.
 */
export function validateDiagnosis(raw: unknown, txn: TracedTransaction): Diagnosis | null {
  if (typeof raw !== 'object' || raw === null) return null
  const o = raw as Record<string, unknown>
  const valid = new Set(txn.evidence.map((e) => e.id))

  const headline = o.headline
  const actionTitle = o.recommendedActionTitle
  const actionReason = o.recommendedActionReason
  if (!isStr(headline) || headline.trim() === '') return null
  if (!isStr(actionTitle) || actionTitle.trim() === '') return null
  if (!isStr(actionReason)) return null

  const weakEvidence = o.weakEvidence === true || txn.evidenceStrength === 'WEAK'

  // Likely cause.
  let likelyCause: CausalClaim | null = null
  const causeText = o.likelyCauseText
  if (isStr(causeText) && causeText.trim() !== '') {
    const confidence = clampConfidence(o.likelyCauseConfidence)
    const ids = validateIds(o.likelyCauseEvidenceIds, valid)
    // A cause citing no real evidence is exactly what we refuse to display.
    if (confidence !== null && ids.length > 0) {
      likelyCause = { text: causeText.trim(), confidence, evidenceIds: ids }
    }
  }

  const alternatives = Array.isArray(o.alternatives)
    ? o.alternatives.map((a) => validateClaim(a, valid)).filter((c): c is CausalClaim => c !== null)
    : []

  // Must end up with something to show.
  if (!likelyCause && alternatives.length === 0) return null

  // Evidence-strength ceiling: an absence-only trace must not be reported with
  // near-certainty even if the model asks for it.
  if (likelyCause && txn.evidenceStrength === 'MODERATE') {
    likelyCause = { ...likelyCause, confidence: Math.min(likelyCause.confidence, 92) }
  }

  // Never let the model advise a retry when money has already moved.
  const moneyMoved = txn.money.settlementCompleted || txn.money.customerCharged
  const retryRecommended = o.retryRecommended === true && !moneyMoved

  return {
    source: 'ai',
    headline: headline.trim(),
    likelyCause: weakEvidence ? null : likelyCause,
    alternatives: weakEvidence ? (alternatives.length > 0 ? alternatives : []) : alternatives,
    // Derived locally -- the model is never the source of a failure code.
    definitiveFailureCode: txn.definitiveFailureCode,
    moneySummary: isStr(o.moneySummary) && o.moneySummary.trim() !== '' ? o.moneySummary.trim() : txn.money.headline,
    recommendedAction: {
      title: actionTitle.trim(),
      reason: actionReason.trim(),
      retryRecommended,
    },
    weakEvidence,
  }
}

/** Cache key for a transaction, so the UI can show whether it is already analysed. */
export function diagnosisKey(
  txn: TracedTransaction,
  cohort: { total: number; settled: number; reversed: number; failed: number },
): string {
  return payloadHash(buildPayload(txn, cohort))
}

export function cachedDiagnosis(
  txn: TracedTransaction,
  cohort: { total: number; settled: number; reversed: number; failed: number },
): Diagnosis | null {
  return readCache(txn.record.id, diagnosisKey(txn, cohort))
}

/**
 * Runs an investigation. Never throws; always returns a renderable diagnosis.
 * `force` re-queries the model even on a cache hit (used by an explicit
 * "re-analyse" action only).
 */
export async function investigate(
  txn: TracedTransaction,
  cohort: { total: number; settled: number; reversed: number; failed: number },
  opts: { force?: boolean } = {},
): Promise<InvestigationResult> {
  const payload = buildPayload(txn, cohort)
  const hash = payloadHash(payload)

  if (!opts.force) {
    const hit = readCache(txn.record.id, hash)
    if (hit) return { diagnosis: hit, fromCache: true, aiError: null, usage: null }
  }

  const fallback = (reason: string): InvestigationResult => ({
    diagnosis: ruleBasedDiagnosis(txn),
    fromCache: false,
    aiError: reason,
    usage: null,
  })

  let res: Response
  try {
    res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch {
    return fallback('The analysis service is unreachable.')
  }

  if (!res.ok) {
    let reason = `Analysis unavailable (${res.status}).`
    try {
      const body = (await res.json()) as { reason?: string }
      if (body.reason) reason = body.reason
    } catch {
      /* keep the generic reason */
    }
    return fallback(reason)
  }

  let body: { diagnosis?: unknown; usage?: { input: number; output: number } }
  try {
    body = (await res.json()) as typeof body
  } catch {
    return fallback('The analysis response could not be read.')
  }

  const diagnosis = validateDiagnosis(body.diagnosis, txn)
  if (!diagnosis) {
    return fallback('The analysis response failed validation.')
  }

  writeCache(txn.record.id, hash, diagnosis)
  return { diagnosis, fromCache: false, aiError: null, usage: body.usage ?? null }
}
