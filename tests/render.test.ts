// Render smoke tests.
//
// renderToString exercises the real component tree for every demo scenario,
// which catches crashes in the render path (bad indexing, missing nodes,
// undefined access) without needing a browser. Effects do not run here, so no
// network call or storage access is involved.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { renderToString } from 'react-dom/server'
import { createElement } from 'react'

import { traceTransaction } from '../src/lib/trace'
import { findSimilar } from '../src/lib/similar'
import { systemHealth } from '../src/lib/health'
import { ruleBasedDiagnosis } from '../src/lib/rulesDiagnosis'

import App from '../src/App'
import { TransactionTimeline } from '../src/components/TransactionTimeline'
import { AIInsightCard } from '../src/components/AIInsightCard'
import { EvidenceDrawer } from '../src/components/EvidenceDrawer'
import { MoneyStatusCard } from '../src/components/MoneyStatusCard'
import { RecommendedAction } from '../src/components/RecommendedAction'
import { SimilarTransactions } from '../src/components/SimilarTransactions'
import { RawEvents } from '../src/components/RawEvents'
import { SystemHealth } from '../src/components/SystemHealth'
import { CommandBar } from '../src/components/CommandBar'

const DEMO_IDS = [
  'TXN_SUCCESS_001',
  'TXN_BANK_TIMEOUT_001',
  'TXN_PROCESSOR_DELAY_001',
  'TXN_LEDGER_DELAY_001',
  'TXN_GATEWAY_FAILED_001',
  'TXN_UNKNOWN_001',
]

test('the full app renders without throwing', () => {
  const html = renderToString(createElement(App))
  assert.ok(html.length > 1000, 'expected substantial markup')
  // The demo labelling is a hard requirement, not decoration.
  assert.match(html, /Demo environment/)
  assert.match(html, /simulated mock data/)
})

test('every demo scenario renders the whole panel set', () => {
  for (const id of DEMO_IDS) {
    const txn = traceTransaction(id)
    assert.ok(txn, `no trace for ${id}`)
    const similar = findSimilar(txn)
    const diagnosis = ruleBasedDiagnosis(txn)
    const noop = () => {}

    const parts: [string, string][] = [
      ['timeline', renderToString(createElement(TransactionTimeline, { txn, animationKey: id }))],
      [
        'insight',
        renderToString(
          createElement(AIInsightCard, {
            txn,
            diagnosis,
            loading: false,
            fromCache: false,
            aiError: null,
            onExplain: noop,
            onAnalyze: noop,
            canAnalyze: true,
          }),
        ),
      ],
      ['money', renderToString(createElement(MoneyStatusCard, { txn, aiSummary: null }))],
      [
        'action',
        renderToString(
          createElement(RecommendedAction, {
            txn,
            diagnosis,
            monitored: false,
            onMonitor: noop,
            onEscalate: noop,
            onViewSimilar: noop,
            similarCount: similar.total,
          }),
        ),
      ],
      [
        'similar',
        renderToString(
          createElement(SimilarTransactions, { summary: similar, expanded: true, onToggle: noop }),
        ),
      ],
      ['raw', renderToString(createElement(RawEvents, { txn, open: true, onToggle: noop }))],
      [
        'command',
        renderToString(
          createElement(CommandBar, {
            txn,
            similar,
            diagnosis,
            onInvestigate: noop,
            investigating: false,
          }),
        ),
      ],
    ]

    for (const [name, html] of parts) {
      assert.ok(html.length > 40, `${id}: ${name} rendered almost nothing`)
    }
  }
})

test('the timeline names all three systems in every scenario', () => {
  for (const id of DEMO_IDS) {
    const txn = traceTransaction(id)
    assert.ok(txn)
    const html = renderToString(createElement(TransactionTimeline, { txn, animationKey: id }))
    for (const system of ['Gateway', 'Bank', 'Ledger']) {
      assert.ok(html.includes(system), `${id}: timeline is missing ${system}`)
    }
  }
})

test('the loading state renders the analysing message', () => {
  const txn = traceTransaction('TXN_BANK_TIMEOUT_001')
  assert.ok(txn)
  const html = renderToString(
    createElement(AIInsightCard, {
      txn,
      diagnosis: null,
      loading: true,
      fromCache: false,
      aiError: null,
      onExplain: () => {},
      onAnalyze: () => {},
      canAnalyze: true,
    }),
  )
  assert.match(html, /Analysing transaction/)
})

test('an AI failure still renders the diagnosis plus an explanation', () => {
  const txn = traceTransaction('TXN_BANK_TIMEOUT_001')
  assert.ok(txn)
  const html = renderToString(
    createElement(AIInsightCard, {
      txn,
      diagnosis: ruleBasedDiagnosis(txn),
      loading: false,
      fromCache: false,
      aiError: 'ANTHROPIC_API_KEY is not set',
      onExplain: () => {},
      onAnalyze: () => {},
      canAnalyze: true,
    }),
  )
  assert.match(html, /Model unavailable/)
  assert.match(html, /Rule-based/)
  // The timeline-derived evidence must survive an AI outage.
  assert.match(html, /Bank acknowledgement missing/)
})

test('the weak-evidence card refuses to name a cause', () => {
  const txn = traceTransaction('TXN_UNKNOWN_001')
  assert.ok(txn)
  const html = renderToString(
    createElement(AIInsightCard, {
      txn,
      diagnosis: ruleBasedDiagnosis(txn),
      loading: false,
      fromCache: false,
      aiError: null,
      onExplain: () => {},
      onAnalyze: () => {},
      canAnalyze: true,
    }),
  )
  assert.match(html, /couldn/i)
  assert.match(html, /possible/i)
})

test('the evidence drawer renders and marks cited records', () => {
  const txn = traceTransaction('TXN_BANK_TIMEOUT_001')
  assert.ok(txn)
  const html = renderToString(
    createElement(EvidenceDrawer, {
      txn,
      onClose: () => {},
      request: {
        kind: 'Likely cause',
        claim: {
          text: 'The bank did not acknowledge the instruction.',
          confidence: 88,
          evidenceIds: ['bank-no-response'],
        },
      },
    }),
  )
  assert.match(html, /AI reasoning evidence/)
  assert.match(html, /AI conclusion/)
  assert.match(html, /cited/)
  assert.match(html, /NO_RESPONSE/)
  assert.match(html, /role="dialog"/)
})

test('the evidence drawer renders nothing when closed', () => {
  const txn = traceTransaction('TXN_BANK_TIMEOUT_001')
  assert.ok(txn)
  const html = renderToString(
    createElement(EvidenceDrawer, { txn, onClose: () => {}, request: null }),
  )
  assert.equal(html, '')
})

test('system health renders all three simulated systems', () => {
  const html = renderToString(createElement(SystemHealth, { health: systemHealth() }))
  for (const system of ['Gateway', 'Bank', 'Ledger']) {
    assert.ok(html.includes(system))
  }
})
