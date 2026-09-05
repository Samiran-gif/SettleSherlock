// Loads the mock logs, normalises them, and indexes them by transaction id.
//
// The CSVs ship with fixed timestamps anchored to a known instant. At load we
// shift every timestamp by one shared offset so the newest event sits ~60s
// before "now" -- that keeps "pending for 2m" honest whenever the demo is run,
// without making the dataset incoherent (one offset, applied uniformly).

import gatewayCsv from '../data/gateway.csv?raw'
import bankCsv from '../data/bank.csv?raw'
import ledgerCsv from '../data/ledger.csv?raw'
import manifestJson from '../data/transactions.json'
import type { LogRow, SystemId, TransactionRecord } from '../types'

/** Minimal RFC-4180-ish parser: handles quoted fields and escaped quotes. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let quoted = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i++
        } else {
          quoted = false
        }
      } else {
        field += ch
      }
      continue
    }
    if (ch === '"') {
      quoted = true
    } else if (ch === ',') {
      row.push(field)
      field = ''
    } else if (ch === '\n') {
      row.push(field)
      field = ''
      if (row.some((c) => c !== '')) rows.push(row)
      row = []
    } else if (ch !== '\r') {
      field += ch
    }
  }
  row.push(field)
  if (row.some((c) => c !== '')) rows.push(row)
  return rows
}

function toLogRows(csv: string): LogRow[] {
  const rows = parseCsv(csv)
  const header = rows[0]
  if (!header) return []
  const idx = {
    transactionId: header.indexOf('transaction_id'),
    timestamp: header.indexOf('timestamp'),
    event: header.indexOf('event'),
    code: header.indexOf('code'),
    detail: header.indexOf('detail'),
  }
  const out: LogRow[] = []
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r]
    if (!row) continue
    const transactionId = row[idx.transactionId] ?? ''
    const timestamp = row[idx.timestamp] ?? ''
    const event = row[idx.event] ?? ''
    if (!transactionId || !timestamp || !event) continue // skip malformed rows rather than crash
    const code = row[idx.code] ?? ''
    out.push({
      transactionId,
      timestamp,
      event,
      code: code === '' ? null : code,
      detail: row[idx.detail] ?? '',
    })
  }
  return out
}

const rawGateway = toLogRows(gatewayCsv)
const rawBank = toLogRows(bankCsv)
const rawLedger = toLogRows(ledgerCsv)
const rawManifest = manifestJson as TransactionRecord[]

// ---------------------------------------------------------------------------
// Demo clock alignment
// ---------------------------------------------------------------------------

function maxTimestamp(): number {
  let max = -Infinity
  for (const list of [rawGateway, rawBank, rawLedger]) {
    for (const row of list) {
      const t = Date.parse(row.timestamp)
      if (Number.isFinite(t) && t > max) max = t
    }
  }
  return max
}

/**
 * Single shared offset, fixed for the lifetime of the page so repeated reads
 * are stable (a drifting offset would make cached AI results disagree with the
 * timeline). Newest event lands 60s before load.
 */
export const CLOCK_OFFSET_MS: number = (() => {
  const max = maxTimestamp()
  if (!Number.isFinite(max)) return 0
  return Date.now() - 60_000 - max
})()

const shift = (iso: string): string => new Date(Date.parse(iso) + CLOCK_OFFSET_MS).toISOString()

function shiftRows(rows: LogRow[]): LogRow[] {
  return rows.map((r) => ({ ...r, timestamp: shift(r.timestamp) }))
}

// ---------------------------------------------------------------------------
// Indexes
// ---------------------------------------------------------------------------

const gateway = shiftRows(rawGateway)
const bank = shiftRows(rawBank)
const ledger = shiftRows(rawLedger)

export const transactions: TransactionRecord[] = rawManifest.map((t) => ({
  ...t,
  createdAt: shift(t.createdAt),
}))

function indexBy(rows: LogRow[]): Map<string, LogRow[]> {
  const m = new Map<string, LogRow[]>()
  for (const row of rows) {
    const list = m.get(row.transactionId)
    if (list) list.push(row)
    else m.set(row.transactionId, [row])
  }
  // Chronological order within each transaction is relied on by the analyzer.
  for (const list of m.values()) list.sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  return m
}

const byId: Record<SystemId, Map<string, LogRow[]>> = {
  gateway: indexBy(gateway),
  bank: indexBy(bank),
  ledger: indexBy(ledger),
}
// ---------------------------------------------------------------------------
// Live backend transactions
// ---------------------------------------------------------------------------

function upsertLog(system: SystemId, row: LogRow): void {
  const existing = byId[system].get(row.transactionId)

  if (existing) {
    const index = existing.findIndex(
      (item) => item.timestamp === row.timestamp && item.event === row.event,
    )

    if (index >= 0) {
      existing[index] = row
    } else {
      existing.push(row)
      existing.sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    }
  } else {
    byId[system].set(row.transactionId, [row])
  }
}

export interface BackendTransactionRecord {
  transaction_id: string
  amount: number
  status: string
  timestamp: string
  reference: string
}

export interface BackendTransactionData {
  transaction_id: string
  gateway: BackendTransactionRecord | null
  bank: BackendTransactionRecord | null
  ledger: BackendTransactionRecord | null
}

function backendEvent(system: SystemId, status: string): string {
  const s = status.toUpperCase()

  if (system === 'gateway') {
    if (s === 'FAILED') return 'REQUEST_REJECTED'
    if (s === 'SUCCESS') return 'REQUEST_ACCEPTED'
    if (s === 'PENDING' || s === 'PROCESSING') return 'REQUEST_ACCEPTED'
  }

  if (system === 'bank') {
    if (s === 'FAILED' || s === 'REJECTED') return 'REJECTED'
    if (s === 'SUCCESS' || s === 'SETTLED') return 'SETTLED'
    if (s === 'PENDING' || s === 'PROCESSING') return 'PROCESSING_DELAYED'
  }

  if (system === 'ledger') {
    if (s === 'REVERSED' || s === 'REVERSE') return 'REVERSAL_RECORDED'
    if (s === 'SUCCESS' || s === 'SETTLED' || s === 'RECONCILED' || s === 'RECORDED') return 'SETTLEMENT_RECORDED'
    if (s === 'PENDING' || s === 'PROCESSING') return 'PROCESSING_DELAYED'
  }

  return 'PROCESSING_DELAYED'
}

function backendDetail(system: SystemId, record: BackendTransactionRecord): string {
  return `${system} status ${record.status}; reference ${record.reference}; amount ${record.amount.toFixed(2)}`
}

/**
 * Inject a transaction returned by the live SettleSherlock backend into the
 * same data structures used by the existing tracing engine.
 */
export function ingestBackendTransaction(data: BackendTransactionData): void {
  const systems: SystemId[] = ['gateway', 'bank', 'ledger']

  for (const system of systems) {
    const source = data[system]

    if (!source) continue

    const event = backendEvent(system, source.status)

    upsertLog(system, {
      transactionId: data.transaction_id,
      timestamp: new Date(source.timestamp).toISOString(),
      event,
      code: null,
      detail: backendDetail(system, source),
    })
  }

  const amount = [data.gateway, data.bank, data.ledger]
    .find((item) => item !== null)?.amount ?? 0

  const createdAt =
    [data.gateway, data.bank, data.ledger]
      .filter((item): item is BackendTransactionRecord => item !== null)
      .map((item) => Date.parse(item.timestamp))
      .filter(Number.isFinite)
      .sort((a, b) => a - b)[0] ?? Date.now()

  const existing = manifestById.get(data.transaction_id)

  if (!existing) {
    const record: TransactionRecord = {
      id: data.transaction_id,
      amountPaise: Math.round(amount * 100),
      currency: 'INR',
      merchantId: 'SETTLESHERLOCK',
      merchantName: 'SettleSherlock Transaction',
      method: 'UNKNOWN',
      createdAt: new Date(createdAt).toISOString(),
      scenario: 'BACKEND',
    }

    transactions.push(record)
    manifestById.set(record.id, record)
  }
}

const manifestById = new Map(transactions.map((t) => [t.id, t]))

export function getTransactionRecord(id: string): TransactionRecord | undefined {
  return manifestById.get(id)
}

export function getLogs(system: SystemId, id: string): LogRow[] {
  return byId[system].get(id) ?? []
}

/** All log rows for a system, used for dataset-wide health derivation. */
export function allLogs(system: SystemId): LogRow[] {
  return system === 'gateway' ? gateway : system === 'bank' ? bank : ledger
}

/**
 * Ids present in any log or the manifest. A transaction can exist in a log
 * without a manifest entry (and vice versa) -- the analyzer handles both.
 */
export function allTransactionIds(): string[] {
  const ids = new Set<string>(transactions.map((t) => t.id))
  for (const system of ['gateway', 'bank', 'ledger'] as SystemId[]) {
    for (const id of byId[system].keys()) ids.add(id)
  }
  return [...ids].sort()
}

/** The curated set surfaced in the demo switcher. */
export const DEMO_TRANSACTION_IDS = [
  'TXN_SUCCESS_001',
  'TXN_BANK_TIMEOUT_001',
  'TXN_PROCESSOR_DELAY_001',
  'TXN_LEDGER_DELAY_001',
  'TXN_GATEWAY_FAILED_001',
  'TXN_UNKNOWN_001',
] as const

