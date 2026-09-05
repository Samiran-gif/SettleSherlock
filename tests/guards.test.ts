// Tests for the two properties that keep this product honest and cheap:
//   1. The model cannot introduce evidence, codes, or unsafe advice.
//   2. Lookup questions never escalate to the model.

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { traceTransaction } from '../src/lib/trace'
import { validateDiagnosis } from '../src/lib/aiClient'
import { buildPayload, payloadHash } from '../src/lib/aiPayload'
import { routeQuestion } from '../src/lib/commands'
import { findSimilar } from '../src/lib/similar'
import { ruleBasedDiagnosis } from '../src/lib/rulesDiagnosis'
import type { TracedTransaction } from '../src/types'

function trace(id: string): TracedTransaction {
  const t = traceTransaction(id)
  assert.ok(t)
  return t
}

const wellFormed = {
  headline: 'The settlement is waiting on the bank.',
  likelyCauseText: 'The bank did not acknowledge the instruction.',
  likelyCauseConfidence: 90,
  likelyCauseEvidenceIds: ['bank-no-response', 'bank-request-sent'],
  alternatives: [],
  moneySummary: 'Money has not settled.',
  recommendedActionTitle: 'Wait for bank confirmation',
  recommendedActionReason: 'The original request may still complete.',
  retryRecommended: false,
  weakEvidence: false,
}

// ---------------------------------------------------------------------------
// Anti-hallucination guards
// ---------------------------------------------------------------------------

test('accepts a well-formed response and marks it as model-sourced', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  const d = validateDiagnosis(wellFormed, txn)
  assert.ok(d)
  assert.equal(d.source, 'ai')
  assert.deepEqual(d.likelyCause?.evidenceIds, ['bank-no-response', 'bank-request-sent'])
})

test('strips evidence ids that do not exist on the trace', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  const d = validateDiagnosis(
    { ...wellFormed, likelyCauseEvidenceIds: ['bank-no-response', 'bank-fraud-detected', 'made-up-id'] },
    txn,
  )
  assert.ok(d)
  assert.deepEqual(d.likelyCause?.evidenceIds, ['bank-no-response'], 'invented ids must be dropped')
})

test('rejects a cause that cites no real evidence at all', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  const d = validateDiagnosis({ ...wellFormed, likelyCauseEvidenceIds: ['totally-invented'] }, txn)
  // No grounded claim and no alternatives -> unusable, caller falls back.
  assert.equal(d, null)
})

test('a failure code is taken from the logs, never from the model', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  const d = validateDiagnosis(
    { ...wellFormed, definitiveFailureCode: 'BNK_TOTALLY_INVENTED' } as unknown,
    txn,
  )
  assert.ok(d)
  assert.equal(d.definitiveFailureCode, null, 'no code exists in these logs')

  const withCode = trace('TXN_PROCESSOR_DELAY_001')
  const d2 = validateDiagnosis(
    { ...wellFormed, likelyCauseEvidenceIds: ['bank-processing-delayed'] },
    withCode,
  )
  assert.ok(d2)
  assert.equal(d2.definitiveFailureCode, 'BNK_QUEUE_BACKLOG', 'taken from the trace')
})

test('confidence is capped when evidence is only moderate', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  assert.equal(txn.evidenceStrength, 'MODERATE')
  const d = validateDiagnosis({ ...wellFormed, likelyCauseConfidence: 100 }, txn)
  assert.ok(d)
  assert.equal(d.likelyCause?.confidence, 92, 'absence-based evidence cannot yield certainty')
})

test('a weak-evidence trace suppresses any single cause the model offers', () => {
  const txn = trace('TXN_UNKNOWN_001')
  const d = validateDiagnosis(
    {
      ...wellFormed,
      likelyCauseEvidenceIds: ['bank-request-sent'],
      weakEvidence: false, // model claims confidence...
      alternatives: [
        { text: 'The bank may not have responded.', confidence: 60, evidenceIds: ['bank-request-sent'] },
        { text: 'Gateway logs may have been dropped.', confidence: 40, evidenceIds: ['gateway-no-events'] },
      ],
    },
    txn,
  )
  assert.ok(d)
  // ...but the trace says WEAK, so the trace wins.
  assert.equal(d.weakEvidence, true)
  assert.equal(d.likelyCause, null)
  assert.equal(d.alternatives.length, 2)
})

test('a retry is never advised once money has moved', () => {
  const settled = trace('TXN_LEDGER_DELAY_001')
  assert.equal(settled.money.settlementCompleted, true)
  const d = validateDiagnosis(
    { ...wellFormed, likelyCauseEvidenceIds: ['bank-settled'], retryRecommended: true },
    settled,
  )
  assert.ok(d)
  assert.equal(d.recommendedAction.retryRecommended, false, 'must override an unsafe retry')
})

test('malformed responses are rejected rather than partially rendered', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  assert.equal(validateDiagnosis(null, txn), null)
  assert.equal(validateDiagnosis('a string', txn), null)
  assert.equal(validateDiagnosis({}, txn), null)
  assert.equal(validateDiagnosis({ ...wellFormed, headline: '' }, txn), null)
  assert.equal(validateDiagnosis({ ...wellFormed, recommendedActionTitle: '' }, txn), null)
  assert.equal(
    validateDiagnosis({ ...wellFormed, likelyCauseConfidence: 'high' }, txn),
    null,
    'non-numeric confidence with no alternatives is unusable',
  )
})

// ---------------------------------------------------------------------------
// Cache key stability -- the property that stops repeat billing
// ---------------------------------------------------------------------------

test('cache key is stable across repeated builds of the same transaction', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  const cohort = { total: 23, settled: 21, reversed: 2, failed: 0 }
  const a = payloadHash(buildPayload(txn, cohort))
  const b = payloadHash(buildPayload(txn, cohort))
  assert.equal(a, b)
  // Cohort counts must not affect the key -- they drift with the clock window.
  const c = payloadHash(buildPayload(txn, { total: 99, settled: 1, reversed: 0, failed: 5 }))
  assert.equal(a, c, 'cohort counts must not invalidate the cache')
})

test('different transactions produce different cache keys', () => {
  const cohort = { total: 0, settled: 0, reversed: 0, failed: 0 }
  const a = payloadHash(buildPayload(trace('TXN_BANK_TIMEOUT_001'), cohort))
  const b = payloadHash(buildPayload(trace('TXN_LEDGER_DELAY_001'), cohort))
  assert.notEqual(a, b)
})

test('payload sends only this transaction, not the corpus', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  const payload = buildPayload(txn, { total: 23, settled: 21, reversed: 2, failed: 0 })
  assert.equal(payload.evidence.length, txn.evidence.length)
  // A tight payload is the cost control: this should stay small.
  assert.ok(JSON.stringify(payload).length < 3000, 'payload should be a few KB at most')
  // Relative offsets only -- no absolute wall-clock timestamps.
  assert.ok(!JSON.stringify(payload).includes('20'), 'no ISO timestamps should be present')
})

// ---------------------------------------------------------------------------
// Command routing -- only causal questions may cost money
// ---------------------------------------------------------------------------

test('lookup questions are answered locally with no AI call', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  const similar = findSimilar(txn)
  const diagnosis = ruleBasedDiagnosis(txn)

  const localQuestions = [
    'How long has it been pending?',
    'Where is the money?',
    'Show me the failed events.',
    'Can I retry this?',
    'Has this happened before?',
    'What happened between the bank and ledger?',
    'What is the status?',
  ]
  for (const q of localQuestions) {
    const a = routeQuestion(q, txn, similar, diagnosis)
    assert.equal(a.kind, 'local', `"${q}" must be answered locally`)
    assert.ok(a.lines.length > 0, `"${q}" produced no answer`)
  }
})

test('only causal questions escalate to the model', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  const similar = findSimilar(txn)
  for (const q of ['Why did this fail?', 'What caused the delay?', 'Explain this transaction']) {
    assert.equal(routeQuestion(q, txn, similar, null).kind, 'ai', `"${q}" should investigate`)
  }
})

test('unrecognised questions do not call the model', () => {
  const txn = trace('TXN_BANK_TIMEOUT_001')
  const similar = findSimilar(txn)
  const a = routeQuestion('what is the weather in mumbai', txn, similar, null)
  assert.equal(a.kind, 'unknown')
  assert.match(a.provenance, /No AI call/i)
})

test('the retry answer reflects real money state, not the model', () => {
  const similar = findSimilar(trace('TXN_LEDGER_DELAY_001'))
  const settled = trace('TXN_LEDGER_DELAY_001')
  const a = routeQuestion('can I retry this?', settled, similar, null)
  assert.match(a.lines.join(' '), /already settled/i)

  const failed = trace('TXN_GATEWAY_FAILED_001')
  const b = routeQuestion('can I retry this?', failed, findSimilar(failed), null)
  assert.match(b.lines.join(' '), /safe/i)
})

// ---------------------------------------------------------------------------
// Rule-based fallback must be renderable for every demo case
// ---------------------------------------------------------------------------

test('rule-based diagnosis is usable for every demo transaction', () => {
  const ids = [
    'TXN_SUCCESS_001',
    'TXN_BANK_TIMEOUT_001',
    'TXN_PROCESSOR_DELAY_001',
    'TXN_LEDGER_DELAY_001',
    'TXN_GATEWAY_FAILED_001',
    'TXN_UNKNOWN_001',
  ]
  for (const id of ids) {
    const txn = trace(id)
    const d = ruleBasedDiagnosis(txn)
    assert.equal(d.source, 'rules')
    assert.ok(d.headline.length > 0, `${id} has no headline`)
    assert.ok(d.recommendedAction.title.length > 0, `${id} has no action`)
    // Either a grounded cause or explicit alternatives -- never nothing.
    assert.ok(
      d.likelyCause !== null || d.alternatives.length > 0,
      `${id} produced neither a cause nor alternatives`,
    )
    // Any cited id must exist on the trace.
    const valid = new Set(txn.evidence.map((e) => e.id))
    for (const cited of d.likelyCause?.evidenceIds ?? []) {
      assert.ok(valid.has(cited), `${id} cites missing evidence ${cited}`)
    }
    // Retry advice must be safe.
    if (d.recommendedAction.retryRecommended) {
      assert.equal(txn.money.settlementCompleted, false, `${id} advises retry after settlement`)
    }
  }
})

test('weak evidence never yields a confident single cause', () => {
  const d = ruleBasedDiagnosis(trace('TXN_UNKNOWN_001'))
  assert.equal(d.weakEvidence, true)
  assert.equal(d.likelyCause, null)
  assert.ok(d.alternatives.length >= 2, 'should offer several possibilities')
  const sum = d.alternatives.reduce((a, x) => a + x.confidence, 0)
  assert.ok(sum >= 95 && sum <= 105, `alternatives should sum to ~100, got ${sum}`)
})
