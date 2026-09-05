/**
 * Deterministic mock-data generator for the settlement investigation demo.
 *
 * Emits three event logs (gateway/bank/ledger) plus a transaction manifest.
 * Every transaction id appears in the manifest and in whichever logs the
 * scenario says it should reach -- that cross-log consistency is what makes
 * the tracing layer meaningful rather than decorative.
 *
 * Run: node scripts/generate-data.mjs
 */
import { writeFileSync, mkdirSync } from 'node:fs'

const OUT = new URL('../src/data/', import.meta.url)
mkdirSync(OUT, { recursive: true })

// Seeded PRNG so regenerating never churns the diff.
function mulberry32(seed) {
  return function () {
    seed |= 0
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const rand = mulberry32(20260905)
const pick = (arr) => arr[Math.floor(rand() * arr.length)]
const between = (lo, hi) => lo + Math.floor(rand() * (hi - lo + 1))

// All timestamps are relative to this fixed anchor; the app shifts them toward
// "now" at load time so pending durations always read sensibly (see clock.ts).
const ANCHOR = Date.parse('2026-09-05T10:00:00.000Z')
const iso = (msFromAnchor) => new Date(ANCHOR + msFromAnchor).toISOString()
const sec = (n) => n * 1000
const min = (n) => n * 60000

const MERCHANTS = [
  ['MER_4417', 'Northwind Retail'],
  ['MER_8823', 'Kirana Junction'],
  ['MER_2094', 'Lotus Diagnostics'],
  ['MER_6610', 'Bluebird Travel'],
  ['MER_3352', 'Sunrise Electronics'],
  ['MER_7781', 'Chai and Co'],
]
const METHODS = ['UPI', 'NEFT', 'IMPS', 'CARD']

const transactions = []
const gateway = []
const bank = []
const ledger = []

/** Register a transaction plus its events. `t0` is ms from ANCHOR. */
function add({ id, t0, amountPaise, scenario, gw = [], bk = [], lg = [], merchant, method }) {
  const chosen = merchant ?? pick(MERCHANTS)
  transactions.push({
    id,
    amountPaise,
    currency: 'INR',
    merchantId: chosen[0],
    merchantName: chosen[1],
    method: method ?? pick(METHODS),
    createdAt: iso(t0),
    // Ground-truth label. Used only for cohort grouping and QA -- it is never
    // surfaced as an AI conclusion, and the analyzer never reads it.
    scenario,
  })
  const push = (target, rows) => {
    for (const row of rows) {
      target.push({
        transaction_id: id,
        timestamp: iso(t0 + row[0]),
        event: row[1],
        code: row[2] ?? '',
        detail: row[3] ?? '',
      })
    }
  }
  push(gateway, gw)
  push(bank, bk)
  push(ledger, lg)
}

// ---------------------------------------------------------------------------
// Hero scenarios -- one per demo case, each with a visibly different timeline.
// ---------------------------------------------------------------------------

// 1. Clean end-to-end settlement.
add({
  id: 'TXN_SUCCESS_001', t0: -min(120), amountPaise: 1899000, scenario: 'SUCCESS',
  merchant: MERCHANTS[0], method: 'NEFT',
  gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request validated'], [sec(1), 'HANDOFF_TO_BANK', 'GW_OK', 'Forwarded to settlement bank']],
  bk: [[sec(4), 'REQUEST_SENT', '', 'Settlement instruction transmitted'], [sec(7), 'ACK_RECEIVED', 'BNK_ACK', 'Bank acknowledged instruction'], [sec(42), 'SETTLED', 'BNK_SETTLED', 'Funds debited and settled']],
  lg: [[sec(45), 'SETTLEMENT_RECORDED', '', 'Settlement posted to ledger'], [min(1), 'RECONCILED', '', 'Matched against bank statement']],
})

// 2. Bank acknowledgement never arrived (the canonical support ticket).
add({
  id: 'TXN_BANK_TIMEOUT_001', t0: -min(2), amountPaise: 2450000, scenario: 'BANK_TIMEOUT',
  merchant: MERCHANTS[1], method: 'IMPS',
  gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request validated'], [sec(1), 'HANDOFF_TO_BANK', 'GW_OK', 'Forwarded to settlement bank']],
  bk: [[sec(4), 'REQUEST_SENT', '', 'Settlement instruction transmitted'], [sec(34), 'NO_RESPONSE', '', 'No acknowledgement within 30s window']],
  lg: [],
})

// 3. Bank acknowledged, then reported its own queue backlog (definitive code).
add({
  id: 'TXN_PROCESSOR_DELAY_001', t0: -min(4), amountPaise: 875050, scenario: 'PROCESSOR_DELAY',
  merchant: MERCHANTS[2], method: 'UPI',
  gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request validated'], [sec(1), 'HANDOFF_TO_BANK', 'GW_OK', 'Forwarded to settlement bank']],
  bk: [[sec(4), 'REQUEST_SENT', '', 'Settlement instruction transmitted'], [sec(7), 'ACK_RECEIVED', 'BNK_ACK', 'Bank acknowledged instruction'], [sec(41), 'PROCESSING_DELAYED', 'BNK_QUEUE_BACKLOG', 'Processor reported batch queue backlog']],
  lg: [],
})

// 4. Bank settled but the ledger has not posted -- money moved, books lag.
add({
  id: 'TXN_LEDGER_DELAY_001', t0: -min(16), amountPaise: 5620000, scenario: 'LEDGER_DELAY',
  merchant: MERCHANTS[3], method: 'NEFT',
  gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request validated'], [sec(1), 'HANDOFF_TO_BANK', 'GW_OK', 'Forwarded to settlement bank']],
  bk: [[sec(4), 'REQUEST_SENT', '', 'Settlement instruction transmitted'], [sec(7), 'ACK_RECEIVED', 'BNK_ACK', 'Bank acknowledged instruction'], [sec(39), 'SETTLED', 'BNK_SETTLED', 'Funds debited and settled']],
  lg: [],
})

// 5. Hard failure at the gateway -- never left our infrastructure.
add({
  id: 'TXN_GATEWAY_FAILED_001', t0: -min(300), amountPaise: 1200000, scenario: 'FAILED_GATEWAY',
  merchant: MERCHANTS[4], method: 'NEFT',
  gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request received'], [sec(1), 'VALIDATION_FAILED', 'GW_INVALID_BENEFICIARY', 'Beneficiary IFSC failed checksum validation']],
  bk: [],
  lg: [],
})

// 6. Incomplete evidence: a bank record with no corresponding gateway log.
//    Deliberately exercises the "could not determine the cause" path.
add({
  id: 'TXN_UNKNOWN_001', t0: -min(95), amountPaise: 3310000, scenario: 'UNKNOWN',
  merchant: MERCHANTS[5], method: 'UPI',
  gw: [],
  bk: [[0, 'REQUEST_SENT', '', 'Settlement instruction transmitted']],
  lg: [],
})

// ---------------------------------------------------------------------------
// Historical cohorts -- give "have we seen this before?" real numbers to
// compute. Each cohort shares a failure signature with one hero scenario.
// ---------------------------------------------------------------------------

let seq = 100
const nextId = (tag) => `TXN_${tag}_${String(++seq).padStart(3, '0')}`

/** Bank-timeout peers that eventually settled after a late ack. */
function bankTimeoutResolved(n) {
  for (let i = 0; i < n; i++) {
    const t0 = -min(between(90, 2600))
    const resolveAt = sec(34) + sec(between(120, 330)) // ~2m-6m after the timeout
    add({
      id: nextId('BT'), t0, amountPaise: between(50000, 9000000), scenario: 'BANK_TIMEOUT_RESOLVED',
      gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request validated'], [sec(1), 'HANDOFF_TO_BANK', 'GW_OK', 'Forwarded to settlement bank']],
      bk: [[sec(4), 'REQUEST_SENT', '', 'Settlement instruction transmitted'], [sec(34), 'NO_RESPONSE', '', 'No acknowledgement within 30s window'], [resolveAt, 'ACK_RECEIVED', 'BNK_ACK', 'Late acknowledgement received'], [resolveAt + sec(6), 'SETTLED', 'BNK_SETTLED', 'Funds debited and settled']],
      lg: [[resolveAt + sec(9), 'SETTLEMENT_RECORDED', '', 'Settlement posted to ledger'], [resolveAt + sec(38), 'RECONCILED', '', 'Matched against bank statement']],
    })
  }
}

/** Bank-timeout peers that were ultimately reversed. */
function bankTimeoutReversed(n) {
  for (let i = 0; i < n; i++) {
    const t0 = -min(between(200, 2600))
    const failAt = sec(34) + sec(between(240, 600))
    add({
      id: nextId('BT'), t0, amountPaise: between(50000, 9000000), scenario: 'BANK_TIMEOUT_REVERSED',
      gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request validated'], [sec(1), 'HANDOFF_TO_BANK', 'GW_OK', 'Forwarded to settlement bank']],
      bk: [[sec(4), 'REQUEST_SENT', '', 'Settlement instruction transmitted'], [sec(34), 'NO_RESPONSE', '', 'No acknowledgement within 30s window'], [failAt, 'REJECTED', 'BNK_INSTRUCTION_EXPIRED', 'Instruction expired before processing']],
      lg: [[failAt + sec(11), 'REVERSAL_RECORDED', '', 'Amount returned to source balance']],
    })
  }
}

function processorDelayCohort(resolved, reversed) {
  for (let i = 0; i < resolved + reversed; i++) {
    const isResolved = i < resolved
    const t0 = -min(between(120, 2800))
    const at = sec(41) + sec(between(90, 400))
    add({
      id: nextId('PD'), t0, amountPaise: between(50000, 7000000),
      scenario: isResolved ? 'PROCESSOR_DELAY_RESOLVED' : 'PROCESSOR_DELAY_REVERSED',
      gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request validated'], [sec(1), 'HANDOFF_TO_BANK', 'GW_OK', 'Forwarded to settlement bank']],
      bk: [[sec(4), 'REQUEST_SENT', '', 'Settlement instruction transmitted'], [sec(7), 'ACK_RECEIVED', 'BNK_ACK', 'Bank acknowledged instruction'], [sec(41), 'PROCESSING_DELAYED', 'BNK_QUEUE_BACKLOG', 'Processor reported batch queue backlog'],
        isResolved ? [at, 'SETTLED', 'BNK_SETTLED', 'Funds debited and settled'] : [at, 'REJECTED', 'BNK_CUTOFF_MISSED', 'Missed settlement cutoff window']],
      lg: isResolved
        ? [[at + sec(4), 'SETTLEMENT_RECORDED', '', 'Settlement posted to ledger'], [at + sec(30), 'RECONCILED', '', 'Matched against bank statement']]
        : [[at + sec(9), 'REVERSAL_RECORDED', '', 'Amount returned to source balance']],
    })
  }
}

function ledgerDelayCohort(n) {
  for (let i = 0; i < n; i++) {
    const t0 = -min(between(150, 2900))
    const post = sec(39) + sec(between(60, 420))
    add({
      id: nextId('LD'), t0, amountPaise: between(80000, 8000000), scenario: 'LEDGER_DELAY_RESOLVED',
      gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request validated'], [sec(1), 'HANDOFF_TO_BANK', 'GW_OK', 'Forwarded to settlement bank']],
      bk: [[sec(4), 'REQUEST_SENT', '', 'Settlement instruction transmitted'], [sec(7), 'ACK_RECEIVED', 'BNK_ACK', 'Bank acknowledged instruction'], [sec(39), 'SETTLED', 'BNK_SETTLED', 'Funds debited and settled']],
      lg: [[post, 'SETTLEMENT_RECORDED', '', 'Settlement posted to ledger (delayed batch)'], [post + sec(25), 'RECONCILED', '', 'Matched against bank statement']],
    })
  }
}

function gatewayFailedCohort(n) {
  const codes = [
    ['GW_INVALID_BENEFICIARY', 'Beneficiary IFSC failed checksum validation'],
    ['GW_LIMIT_EXCEEDED', 'Merchant per-transaction limit exceeded'],
    ['GW_KYC_INCOMPLETE', 'Merchant KYC record incomplete'],
  ]
  for (let i = 0; i < n; i++) {
    const t0 = -min(between(100, 3000))
    const c = pick(codes)
    add({
      id: nextId('GF'), t0, amountPaise: between(50000, 5000000), scenario: 'FAILED_GATEWAY',
      gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request received'], [sec(between(1, 3)), 'VALIDATION_FAILED', c[0], c[1]]],
      bk: [], lg: [],
    })
  }
}

function successCohort(n) {
  for (let i = 0; i < n; i++) {
    const t0 = -min(between(60, 3000))
    const settle = sec(between(30, 90))
    add({
      id: nextId('OK'), t0, amountPaise: between(50000, 9500000), scenario: 'SUCCESS',
      gw: [[0, 'REQUEST_ACCEPTED', 'GW_OK', 'Payout request validated'], [sec(1), 'HANDOFF_TO_BANK', 'GW_OK', 'Forwarded to settlement bank']],
      bk: [[sec(4), 'REQUEST_SENT', '', 'Settlement instruction transmitted'], [sec(7), 'ACK_RECEIVED', 'BNK_ACK', 'Bank acknowledged instruction'], [settle, 'SETTLED', 'BNK_SETTLED', 'Funds debited and settled']],
      lg: [[settle + sec(3), 'SETTLEMENT_RECORDED', '', 'Settlement posted to ledger'], [settle + sec(28), 'RECONCILED', '', 'Matched against bank statement']],
    })
  }
}

/** Orphaned bank records with no gateway log -- the weak-evidence cohort. */
function unknownCohort(n) {
  for (let i = 0; i < n; i++) {
    const t0 = -min(between(180, 2400))
    add({
      id: nextId('UNK'), t0, amountPaise: between(60000, 4000000), scenario: 'UNKNOWN',
      gw: [], bk: [[0, 'REQUEST_SENT', '', 'Settlement instruction transmitted']], lg: [],
    })
  }
}

// Cohort sizes are chosen so the hero bank-timeout case has 23 peers
// (21 settled / 2 reversed) -- statistics the UI computes, never hardcodes.
bankTimeoutResolved(21)
bankTimeoutReversed(2)
processorDelayCohort(12, 2)
ledgerDelayCohort(9)
gatewayFailedCohort(11)
successCohort(24)
unknownCohort(4)

// ---------------------------------------------------------------------------
// Emit
// ---------------------------------------------------------------------------
const CSV_COLS = ['transaction_id', 'timestamp', 'event', 'code', 'detail']
function toCsv(rows) {
  const esc = (v) => (/[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v))
  const body = rows
    .slice()
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp) || a.transaction_id.localeCompare(b.transaction_id))
    .map((r) => CSV_COLS.map((c) => esc(r[c])).join(','))
  return [CSV_COLS.join(','), ...body].join('\n') + '\n'
}

writeFileSync(new URL('gateway.csv', OUT), toCsv(gateway))
writeFileSync(new URL('bank.csv', OUT), toCsv(bank))
writeFileSync(new URL('ledger.csv', OUT), toCsv(ledger))
transactions.sort((a, b) => a.id.localeCompare(b.id))
writeFileSync(new URL('transactions.json', OUT), JSON.stringify(transactions, null, 2) + '\n')

console.log(
  'transactions ' + transactions.length +
  '  gateway ' + gateway.length +
  '  bank ' + bank.length +
  '  ledger ' + ledger.length
)
