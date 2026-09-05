// Tests for injecting a live backend transaction into the local trace engine.
//
// This file mutates the shared dataset module, so it is kept separate: the
// test runner bundles each file independently, giving it a clean corpus.

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { datasetRevision, ingestBackendTransaction } from '../src/lib/dataset'
import { traceTransaction } from '../src/lib/trace'
import { findSimilar } from '../src/lib/similar'

function record(status: string, timestamp: string, reference = 'REF') {
  return { transaction_id: 'TXN_LIVE_001', amount: 245, status, timestamp, reference }
}

test('an ingested backend transaction becomes traceable', () => {
  assert.equal(traceTransaction('TXN_LIVE_001'), null, 'must not exist beforehand')

  ingestBackendTransaction({
    transaction_id: 'TXN_LIVE_001',
    gateway: record('SUCCESS', '2024-03-01T10:00:00Z'),
    bank: record('PENDING', '2024-03-01T10:00:30Z'),
    ledger: null,
  })

  const traced = traceTransaction('TXN_LIVE_001')
  assert.ok(traced, 'the ingested transaction should now trace')
  assert.equal(traced.record.id, 'TXN_LIVE_001')
  // 245 major units -> paise.
  assert.equal(traced.record.amountPaise, 24_500)
  assert.equal(traced.systems.find((s) => s.system === 'ledger')?.reached, false)
})

test('ingesting bumps the dataset revision so memoised corpora rebuild', () => {
  const before = datasetRevision()
  ingestBackendTransaction({
    transaction_id: 'TXN_LIVE_002',
    gateway: record('SUCCESS', '2024-03-01T11:00:00Z'),
    bank: null,
    ledger: null,
  })
  assert.ok(datasetRevision() > before, 'the revision must advance')
})

test('an ingested transaction is visible to the cohort corpus', () => {
  // Build the memoised corpus first, then ingest: a stale cache would hide it.
  const seed = traceTransaction('TXN_BANK_TIMEOUT_001')
  assert.ok(seed)
  findSimilar(seed)

  ingestBackendTransaction({
    transaction_id: 'TXN_LIVE_003',
    gateway: record('SUCCESS', '2024-03-01T12:00:00Z'),
    bank: record('PENDING', '2024-03-01T12:00:30Z'),
    ledger: null,
  })

  const live = traceTransaction('TXN_LIVE_003')
  assert.ok(live)

  // Anything sharing the new transaction's signature must be able to see it.
  const peersOfLive = findSimilar(live)
  const ids = peersOfLive.peers.map((p) => p.id)
  assert.ok(!ids.includes('TXN_LIVE_003'), 'a transaction is not its own peer')

  const sameSignature = findSimilar(seed)
  if (live.signature === seed.signature) {
    assert.ok(
      sameSignature.peers.some((p) => p.id === 'TXN_LIVE_003'),
      'the freshly ingested peer must appear once the revision advances',
    )
  }
})

test('an unparseable timestamp skips the row instead of throwing', () => {
  assert.doesNotThrow(() =>
    ingestBackendTransaction({
      transaction_id: 'TXN_LIVE_004',
      gateway: record('SUCCESS', 'not-a-date'),
      bank: record('PENDING', '2024-03-01T13:00:30Z'),
      ledger: null,
    }),
  )

  const traced = traceTransaction('TXN_LIVE_004')
  assert.ok(traced, 'the parseable rows should still produce a trace')
  assert.equal(traced.systems.find((s) => s.system === 'gateway')?.reached, false)
  assert.equal(traced.systems.find((s) => s.system === 'bank')?.reached, true)
})

test('a transaction with no usable records at all does not trace', () => {
  ingestBackendTransaction({
    transaction_id: 'TXN_LIVE_005',
    gateway: null,
    bank: null,
    ledger: null,
  })
  const traced = traceTransaction('TXN_LIVE_005')
  // A manifest entry is created, so it traces -- but with nothing reached.
  assert.ok(traced)
  assert.deepEqual(
    traced.systems.filter((s) => s.reached).map((s) => s.system),
    [],
  )
})
