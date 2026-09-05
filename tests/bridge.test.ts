// Tests for the server-side SettleSherlock bridge (server/analyze.ts).
//
// These spin up a throwaway http server rather than mocking fetch, so the
// reachability logic is exercised for real. No external network is touched.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'

import { aiStatus, resetAiStatusCache, runAnalysis } from '../server/analyze'

type Handler = (path: string) => { status: number; body: unknown } | null

/** Starts a server on an ephemeral port and returns its base URL. */
async function serve(handler: Handler): Promise<{ url: string; close: () => Promise<void> }> {
  const server: Server = createServer((req, res) => {
    const reply = handler(req.url ?? '')
    if (!reply) {
      res.statusCode = 404
      res.end('{}')
      return
    }
    res.statusCode = reply.status
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify(reply.body))
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  return {
    url: `http://127.0.0.1:${port}`,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  }
}

/** A port nothing is listening on, so connections are refused immediately. */
async function deadUrl(): Promise<string> {
  const s = await serve(() => null)
  const url = s.url
  await s.close()
  return url
}

const MONEY = {
  customerCharged: true,
  bankAuthorized: false,
  settlementCompleted: false,
  ledgerUpdated: false,
  reversed: false,
}

function payload(evidence: unknown[]): Record<string, unknown> {
  return {
    id: 'TXN_TEST_001',
    amountPaise: 100_00,
    evidenceStrength: 'STRONG',
    definitiveFailureCode: null,
    money: MONEY,
    evidence,
  }
}

// ---------------------------------------------------------------------------
// aiStatus must actually reach the backend
// ---------------------------------------------------------------------------

test('aiStatus reports unavailable when nothing is listening', async () => {
  resetAiStatusCache()
  const url = await deadUrl()
  const status = await aiStatus({ SETTLESHERLOCK_BACKEND_URL: url })
  assert.equal(status.available, false, 'a refused connection must not read as ready')
  assert.match(status.reason, /Cannot reach/i)
})

test('aiStatus reports available when the backend answers at all', async () => {
  resetAiStatusCache()
  const s = await serve(() => ({ status: 404, body: {} }))
  try {
    // A 404 still proves something is listening.
    const status = await aiStatus({ SETTLESHERLOCK_BACKEND_URL: s.url })
    assert.equal(status.available, true)
    assert.match(status.reason, /Connected/i)
  } finally {
    await s.close()
  }
})

test('aiStatus does not reuse a cached result across different urls', async () => {
  resetAiStatusCache()
  const live = await serve(() => ({ status: 200, body: { ok: true } }))
  try {
    const up = await aiStatus({ SETTLESHERLOCK_BACKEND_URL: live.url })
    assert.equal(up.available, true)

    const down = await aiStatus({ SETTLESHERLOCK_BACKEND_URL: await deadUrl() })
    assert.equal(down.available, false, 'the cache must be keyed on the url')
  } finally {
    await live.close()
  }
})

// ---------------------------------------------------------------------------
// Request validation happens before the backend is blamed
// ---------------------------------------------------------------------------

test('a payload with no transaction id is rejected as a bad request', async () => {
  const r = await runAnalysis({ money: MONEY, evidence: [] }, {})
  assert.equal(r.status, 400)
})

test('a payload with no evidence array is a bad request, not a backend outage', async () => {
  const r = await runAnalysis({ id: 'TXN_TEST_001', money: MONEY }, {})
  assert.equal(r.status, 400)
  assert.deepEqual((r.body as { error: string }).error, 'invalid_evidence')
})

test('a payload with no money state is a bad request', async () => {
  const r = await runAnalysis({ id: 'TXN_TEST_001', evidence: [] }, {})
  assert.equal(r.status, 400)
  assert.deepEqual((r.body as { error: string }).error, 'invalid_money_state')
})

test('an unreachable backend yields 503, not a thrown error', async () => {
  const r = await runAnalysis(payload([]), {
    SETTLESHERLOCK_BACKEND_URL: await deadUrl(),
  })
  assert.equal(r.status, 503)
  assert.deepEqual((r.body as { error: string }).error, 'backend_unavailable')
})

// ---------------------------------------------------------------------------
// Evidence citations must point at the record the statement describes
// ---------------------------------------------------------------------------

test('a citation does not match an unrelated record via an empty code', async () => {
  const s = await serve((path) => {
    if (path.includes('/investigation')) {
      return {
        status: 200,
        body: {
          transaction_id: 'TXN_TEST_001',
          status: 'FAILED',
          root_cause: 'The bank rejected the instruction',
          investigation_confidence: 90,
          // Names the event explicitly, so the exact row is identifiable.
          evidence: ['Bank status is REJECTED'],
          exceptions: [],
          recommended_action: 'Review the rejection',
        },
      }
    }
    const record = {
      transaction_id: 'TXN_TEST_001',
      amount: 100,
      status: 'FAILED',
      timestamp: '2024-01-01T00:00:00Z',
      reference: 'REF1',
    }
    return { status: 200, body: { transaction_id: 'TXN_TEST_001', gateway: record, bank: record, ledger: record } }
  })

  try {
    // The first bank row has no code and an empty detail: under the old
    // `includes('')` match it swallowed every bank statement.
    const r = await runAnalysis(
      payload([
        { id: 'bank-request-sent', system: 'bank', kind: 'event', event: 'REQUEST_SENT', code: null, detail: '' },
        { id: 'bank-rejected', system: 'bank', kind: 'event', event: 'REJECTED', code: 'BANK_DECLINE', detail: 'declined' },
      ]),
      { SETTLESHERLOCK_BACKEND_URL: s.url },
    )

    assert.equal(r.status, 200)
    const diagnosis = (r.body as { diagnosis: Record<string, unknown> }).diagnosis
    assert.deepEqual(
      diagnosis.likelyCauseEvidenceIds,
      ['bank-rejected'],
      'the statement names REJECTED, so that is the row it must cite',
    )
  } finally {
    await s.close()
  }
})
