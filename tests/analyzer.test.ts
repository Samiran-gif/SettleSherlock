import { test } from 'node:test'
import assert from 'node:assert/strict'

import { traceTransaction, SYSTEM_ORDER } from '../src/lib/trace'
import { findSimilar } from '../src/lib/similar'
import { systemHealth } from '../src/lib/health'
import { formatCurrency, formatDuration } from '../src/lib/format'
import { allTransactionIds } from '../src/lib/dataset'
import type { NodeState, SystemId, TracedTransaction } from '../src/types'

function trace(id: string): TracedTransaction {
  const t = traceTransaction(id)
  assert.ok(t, `expected a trace for ${id}`)
  return t
}

function stateOf(t: TracedTransaction, system: SystemId): NodeState {
  const node = t.systems.find((s) => s.system === system)
  assert.ok(node, `missing ${system} node`)
  return node.state
}

function evidenceIds(t: TracedTransaction): string[] {
  return t.evidence.map((e) => e.id)
}

// ---------------------------------------------------------------------------
// Scenario 1: clean settlement
// ---------------------------------------------------------------------------
test('SUCCESS: settles end to end with every node green', () => {
  const t = trace('TXN_SUCCESS_001')
  assert.equal(t.status, 'SETTLED')
  assert.equal(stateOf(t, 'gateway'), 'SUCCESS')
  assert.equal(stateOf(t, 'bank'), 'SUCCESS')
  assert.equal(stateOf(t, 'ledger'), 'SUCCESS')
  assert.equal(t.stoppedAt, null)
  assert.equal(t.pendingForMs, null)
  assert.equal(t.signature, 'none:NONE')
  assert.equal(t.evidenceStrength, 'STRONG')
  assert.equal(t.definitiveFailureCode, null)

  assert.equal(t.money.customerCharged, true)
  assert.equal(t.money.bankAuthorized, true)
  assert.equal(t.money.settlementCompleted, true)
  assert.equal(t.money.ledgerUpdated, true)
  assert.equal(t.money.reversed, false)
})

// ---------------------------------------------------------------------------
// Scenario 2: bank acknowledgement timed out (the canonical case)
// ---------------------------------------------------------------------------
test('BANK_TIMEOUT: stalls at the bank with the ledger never reached', () => {
  const t = trace('TXN_BANK_TIMEOUT_001')
  assert.equal(t.status, 'PENDING')
  assert.equal(stateOf(t, 'gateway'), 'SUCCESS')
  assert.equal(stateOf(t, 'bank'), 'PENDING')
  assert.equal(stateOf(t, 'ledger'), 'UNKNOWN')
  assert.equal(t.stoppedAt, 'bank')
  assert.equal(t.signature, 'bank:NO_RESPONSE')

  // No failure code exists in the logs, so none may be reported.
  assert.equal(t.definitiveFailureCode, null)
  assert.equal(t.evidenceStrength, 'MODERATE')

  // The exact evidence the spec calls for, including the absence markers.
  const ids = evidenceIds(t)
  assert.ok(ids.includes('gateway-request-accepted'))
  assert.ok(ids.includes('bank-request-sent'))
  assert.ok(ids.includes('bank-no-response'))
  assert.ok(ids.includes('ledger-no-events'))

  assert.equal(t.money.bankAuthorized, false)
  assert.equal(t.money.settlementCompleted, false)
  assert.equal(t.money.ledgerUpdated, false)
  assert.match(t.money.headline, /duplicate/i)

  assert.ok(t.pendingForMs !== null && t.pendingForMs > 0, 'should report a pending duration')
})

// ---------------------------------------------------------------------------
// Scenario 3: processor delay, with a real failure code
// ---------------------------------------------------------------------------
test('PROCESSOR_DELAY: bank is processing and reports a definitive code', () => {
  const t = trace('TXN_PROCESSOR_DELAY_001')
  assert.equal(t.status, 'PENDING')
  assert.equal(stateOf(t, 'bank'), 'PROCESSING')
  assert.equal(t.signature, 'bank:PROCESSING_DELAYED')
  assert.equal(t.definitiveFailureCode, 'BNK_QUEUE_BACKLOG')
  assert.equal(t.evidenceStrength, 'STRONG')
  assert.equal(t.money.bankAuthorized, true, 'bank acknowledged before delaying')
  assert.equal(t.money.settlementCompleted, false)
})

// ---------------------------------------------------------------------------
// Scenario 4: money settled, books lagging
// ---------------------------------------------------------------------------
test('LEDGER_DELAY: settled at the bank but not posted to the ledger', () => {
  const t = trace('TXN_LEDGER_DELAY_001')
  assert.equal(stateOf(t, 'bank'), 'SUCCESS')
  assert.equal(stateOf(t, 'ledger'), 'PENDING')
  assert.equal(t.status, 'PENDING')
  assert.equal(t.stoppedAt, 'ledger')
  assert.equal(t.signature, 'ledger:LEDGER_LAG')

  // The critical distinction: the money HAS moved even though the txn is open.
  assert.equal(t.money.settlementCompleted, true)
  assert.equal(t.money.ledgerUpdated, false)
  assert.match(t.money.headline, /do not re-initiate/i)
})

// ---------------------------------------------------------------------------
// Scenario 5: hard gateway failure
// ---------------------------------------------------------------------------
test('FAILED_GATEWAY: never reaches the bank, so downstream is unavailable', () => {
  const t = trace('TXN_GATEWAY_FAILED_001')
  assert.equal(t.status, 'FAILED')
  assert.equal(stateOf(t, 'gateway'), 'FAILED')
  assert.equal(stateOf(t, 'bank'), 'UNKNOWN')
  assert.equal(stateOf(t, 'ledger'), 'UNKNOWN')
  assert.equal(t.definitiveFailureCode, 'GW_INVALID_BENEFICIARY')
  assert.equal(t.signature, 'gateway:VALIDATION_FAILED')

  const bank = t.systems.find((s) => s.system === 'bank')
  assert.equal(bank?.reached, false, 'bank node must render as never reached')

  assert.equal(t.money.customerCharged, false, 'no money moves on a gateway rejection')
  assert.equal(t.money.settlementCompleted, false)
  assert.equal(t.pendingForMs, null, 'terminal state is not pending')
})

// ---------------------------------------------------------------------------
// Scenario 6: incomplete evidence
// ---------------------------------------------------------------------------
test('UNKNOWN: missing gateway log yields weak evidence, not a guess', () => {
  const t = trace('TXN_UNKNOWN_001')
  assert.equal(t.status, 'UNKNOWN')
  assert.equal(stateOf(t, 'gateway'), 'UNKNOWN')
  assert.equal(t.evidenceStrength, 'WEAK')
  assert.deepEqual(t.missingLogs, ['gateway', 'ledger'])
  assert.equal(t.signature, 'gateway:MISSING_GATEWAY_LOG')
  assert.equal(t.definitiveFailureCode, null)
  assert.match(t.money.headline, /cannot be confirmed/i)
})

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------
test('unknown transaction id returns null rather than throwing', () => {
  assert.equal(traceTransaction('TXN_DOES_NOT_EXIST'), null)
  assert.equal(traceTransaction(''), null)
})

test('every traced transaction has unique evidence ids', () => {
  for (const id of allTransactionIds()) {
    const t = traceTransaction(id)
    if (!t) continue
    const ids = evidenceIds(t)
    assert.equal(new Set(ids).size, ids.length, `duplicate evidence id in ${id}`)
  }
})

test('systems are always ordered gateway -> bank -> ledger', () => {
  const t = trace('TXN_SUCCESS_001')
  assert.deepEqual(
    t.systems.map((s) => s.system),
    SYSTEM_ORDER,
  )
})

// ---------------------------------------------------------------------------
// Cohort statistics -- must be computed, never hardcoded
// ---------------------------------------------------------------------------
test('similar transactions: bank timeout cohort resolves mostly favourably', () => {
  const t = trace('TXN_BANK_TIMEOUT_001')
  const s = findSimilar(t)
  assert.equal(s.total, 23, 'seeded cohort size')
  assert.equal(s.settled, 21)
  assert.equal(s.reversed, 2)
  assert.ok(s.avgResolutionMs !== null && s.avgResolutionMs > 0)
  assert.ok(!s.peers.some((p) => p.id === t.record.id), 'must exclude itself')
})

test('similar transactions: processor delay cohort', () => {
  const s = findSimilar(trace('TXN_PROCESSOR_DELAY_001'))
  assert.equal(s.total, 14)
  assert.equal(s.settled, 12)
  assert.equal(s.reversed, 2)
})

test('similar transactions: gateway failures never settle', () => {
  const s = findSimilar(trace('TXN_GATEWAY_FAILED_001'))
  assert.equal(s.total, 11)
  assert.equal(s.failed, 11)
  assert.equal(s.settled, 0)
})

test('similar transactions: weak-evidence cohort is found by its own signature', () => {
  const s = findSimilar(trace('TXN_UNKNOWN_001'))
  assert.equal(s.total, 4)
})

// ---------------------------------------------------------------------------
// Health + formatting
// ---------------------------------------------------------------------------
test('system health reports all three systems', () => {
  const h = systemHealth()
  assert.deepEqual(
    h.map((x) => x.system),
    SYSTEM_ORDER,
  )
  for (const entry of h) {
    assert.ok(['OPERATIONAL', 'DELAYED', 'DEGRADED'].includes(entry.state))
    assert.ok(entry.detail.length > 0)
  }
})

test('currency formatting uses INR and paise precision', () => {
  assert.equal(formatCurrency(2450000), '₹24,500')
  assert.equal(formatCurrency(875050), '₹8,750.50')
})

test('duration formatting is compact', () => {
  assert.equal(formatDuration(222000), '3m 42s')
  assert.equal(formatDuration(6000), '6s')
  assert.equal(formatDuration(3600000), '1h')
  assert.equal(formatDuration(-1), '--')
})
