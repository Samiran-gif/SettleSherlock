// Deterministic, rule-based diagnosis.
//
// Two jobs:
//   1. The fallback whenever the model is unavailable, disabled, or returns
//      something invalid -- so the console is never visibly broken.
//   2. The development path, so the UI can be built and tested without
//      spending any API credit.
//
// Every cause it states is tied to evidence ids that actually exist on the
// trace, and confidence is a function of evidence strength -- never optimism.

import { corpus } from './similar'
import { formatDuration } from './format'
import type { CausalClaim, Diagnosis, EvidenceItem, RecommendedAction, TracedTransaction } from '../types'

/** Keep only ids that exist, so a claim can never cite invented evidence. */
function pickEvidence(txn: TracedTransaction, ...ids: string[]): string[] {
  const available = new Set(txn.evidence.map((e) => e.id))
  return ids.filter((id) => available.has(id))
}

/** All ids for a system, useful when the whole log is the evidence. */
function systemEvidence(txn: TracedTransaction, system: EvidenceItem['system']): string[] {
  return txn.evidence.filter((e) => e.system === system).map((e) => e.id)
}

/**
 * When evidence is too thin for a single cause, weight the plausible
 * explanations by how often each incident actually occurs in the dataset
 * among transactions that got at least as far as this one. Computed locally.
 */
function corpusAlternatives(txn: TracedTransaction): CausalClaim[] {
  const reachedBank = txn.systems.find((s) => s.system === 'bank')?.reached ?? false
  const peers = corpus().filter((t) => {
    if (t.record.id === txn.record.id) return false
    const bank = t.systems.find((s) => s.system === 'bank')
    return reachedBank ? (bank?.reached ?? false) : true
  })

  const counts = new Map<string, number>()
  for (const p of peers) {
    // Group by the incident marker, ignoring transactions that were clean.
    const key = p.incident.marker === 'NONE' ? 'SETTLED_NORMALLY' : p.incident.marker
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }

  const TEXT: Record<string, string> = {
    NO_RESPONSE: 'The bank did not acknowledge the instruction in time.',
    PROCESSING_DELAYED: 'The bank accepted the instruction but delayed processing.',
    REJECTED: 'The bank rejected the instruction.',
    VALIDATION_FAILED: 'The request failed validation at the gateway.',
    LEDGER_LAG: 'The bank settled but the ledger has not posted the entry.',
    MISSING_GATEWAY_LOG: 'Gateway logs for this transaction were never recorded.',
    SETTLED_NORMALLY: 'The transaction is progressing normally and is not yet complete.',
  }

  const total = [...counts.values()].reduce((a, b) => a + b, 0)
  if (total === 0) return []

  const ranked = [...counts.entries()]
    .filter(([k]) => TEXT[k] !== undefined)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)

  const shown = ranked.reduce((a, [, n]) => a + n, 0)
  return ranked.map(([key, n]) => ({
    text: TEXT[key] ?? key,
    // Normalised across the shown subset so the figures add to ~100.
    confidence: Math.round((n / shown) * 100),
    evidenceIds: systemEvidence(txn, 'bank').slice(0, 2),
  }))
}

function action(title: string, reason: string, retryRecommended: boolean): RecommendedAction {
  return { title, reason, retryRecommended }
}

/** The deterministic diagnosis. Mirrors the shape the model is asked for. */
export function ruleBasedDiagnosis(txn: TracedTransaction): Diagnosis {
  const { signature, evidenceStrength, definitiveFailureCode, money } = txn
  const pendingFor = txn.pendingForMs !== null ? formatDuration(txn.pendingForMs) : null

  const base = {
    source: 'rules' as const,
    definitiveFailureCode,
    moneySummary: money.headline,
    alternatives: [] as CausalClaim[],
    weakEvidence: false,
  }

  // Weak evidence: refuse to name a single cause.
  if (evidenceStrength === 'WEAK') {
    return {
      ...base,
      headline:
        txn.missingLogs.includes('gateway')
          ? 'There is no gateway record for this transaction, so its start cannot be verified.'
          : 'There are too few recorded events to determine what happened.',
      likelyCause: null,
      alternatives: corpusAlternatives(txn),
      weakEvidence: true,
      recommendedAction: action(
        'Request a log replay',
        'The available records are incomplete, so any single cause would be a guess. Recover the missing log before acting.',
        false,
      ),
    }
  }

  switch (signature) {
    case 'none:NONE':
      return {
        ...base,
        headline: 'The settlement completed normally and is reconciled in the ledger.',
        likelyCause: {
          text: 'The transaction settled end to end with no anomalies.',
          confidence: 99,
          evidenceIds: pickEvidence(txn, 'bank-settled', 'ledger-settlement-recorded', 'ledger-reconciled'),
        },
        recommendedAction: action('No action needed', 'The settlement is complete and reconciled.', false),
      }

    case 'bank:NO_RESPONSE':
      return {
        ...base,
        headline: pendingFor
          ? `The settlement is waiting for a bank confirmation that has been outstanding for ${pendingFor}.`
          : 'The settlement is waiting for a bank confirmation.',
        likelyCause: {
          text: 'The bank did not acknowledge the settlement instruction within the expected window.',
          // No failure code exists here, so this stays short of certainty.
          confidence: 88,
          evidenceIds: pickEvidence(
            txn,
            'gateway-request-accepted',
            'bank-request-sent',
            'bank-no-response',
            'ledger-no-events',
          ),
        },
        recommendedAction: action(
          'Wait for bank confirmation',
          'The original instruction may still complete. Retrying now risks a duplicate payout.',
          false,
        ),
      }

    case 'bank:PROCESSING_DELAYED':
      return {
        ...base,
        headline: 'The bank accepted the instruction and then reported a processing delay on its side.',
        likelyCause: {
          text: 'The bank reported a batch queue backlog after acknowledging the instruction.',
          // A real code from the logs supports this one.
          confidence: 94,
          evidenceIds: pickEvidence(txn, 'bank-ack-received', 'bank-processing-delayed', 'ledger-no-events'),
        },
        recommendedAction: action(
          'Monitor until the next batch window',
          'The bank has acknowledged the instruction and reported its own backlog, so it is expected to clear without intervention.',
          false,
        ),
      }

    case 'bank:REJECTED':
      return {
        ...base,
        headline: 'The bank rejected the settlement instruction.',
        likelyCause: {
          text: definitiveFailureCode
            ? `The bank rejected the instruction with code ${definitiveFailureCode}.`
            : 'The bank rejected the instruction.',
          confidence: 96,
          evidenceIds: pickEvidence(txn, 'bank-rejected', 'bank-request-sent'),
        },
        recommendedAction: action(
          'Escalate to the bank',
          'A rejection is terminal at the bank. Confirm the reason before re-submitting.',
          true,
        ),
      }

    case 'gateway:VALIDATION_FAILED':
    case 'gateway:REQUEST_REJECTED':
      return {
        ...base,
        headline: 'The request was rejected by the gateway and never reached the bank.',
        likelyCause: {
          text: definitiveFailureCode
            ? `The gateway rejected the request during validation with code ${definitiveFailureCode}.`
            : 'The gateway rejected the request during validation.',
          confidence: 97,
          evidenceIds: pickEvidence(txn, 'gateway-request-accepted', 'gateway-validation-failed', 'bank-no-events'),
        },
        recommendedAction: action(
          'Correct the payout details and re-submit',
          'No money moved, so a corrected re-submission is safe.',
          true,
        ),
      }

    case 'ledger:LEDGER_LAG':
      return {
        ...base,
        headline: 'The money has settled at the bank, but the ledger has not recorded the entry yet.',
        likelyCause: {
          text: 'The bank confirmed settlement and the ledger entry has not posted, indicating a ledger-side delay.',
          confidence: 91,
          evidenceIds: pickEvidence(txn, 'bank-settled', 'ledger-no-events'),
        },
        recommendedAction: action(
          'Escalate to reconciliation',
          'The funds have already moved, so this is a bookkeeping gap rather than a payment failure. Do not re-initiate.',
          false,
        ),
      }

    default:
      // Unrecognised signature: describe the stop point without inventing a cause.
      return {
        ...base,
        headline: txn.stoppedAt
          ? `The transaction stopped progressing at the ${txn.stoppedAt}.`
          : 'The transaction state could not be classified.',
        likelyCause: null,
        alternatives: corpusAlternatives(txn),
        weakEvidence: true,
        recommendedAction: action(
          'Review the raw events',
          'This combination of events does not match a known pattern.',
          false,
        ),
      }
  }
}
