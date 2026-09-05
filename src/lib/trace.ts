// Deterministic transaction tracing.
//
// Correlates the three logs into one TracedTransaction: node states, money
// state, the incident signature, and a set of citable evidence items. This is
// the single source of truth for the UI, and the only thing the AI is allowed
// to reason over. No AI call happens here.

import { getLogs, getTransactionRecord } from './dataset'
import { kebab } from './format'
import type {
  EvidenceItem,
  EvidenceStrength,
  Incident,
  LogRow,
  MoneyState,
  NodeState,
  SystemId,
  SystemTrace,
  TracedTransaction,
  TransactionRecord,
  TxnStatus,
} from '../types'

export const SYSTEM_ORDER: SystemId[] = ['gateway', 'bank', 'ledger']

export const SYSTEM_LABEL: Record<SystemId, string> = {
  gateway: 'Gateway',
  bank: 'Bank',
  ledger: 'Ledger',
}

/** If the ledger has not posted this long after the bank settled, it is lagging. */
const LEDGER_LAG_THRESHOLD_MS = 30_000

/** Events that mark something going wrong, in the order we would encounter them. */
const ANOMALY_EVENTS = new Set([
  'VALIDATION_FAILED',
  'REQUEST_REJECTED',
  'NO_RESPONSE',
  'PROCESSING_DELAYED',
  'REJECTED',
])

/** Human statement + tone for each known event. Unknown events fall back gracefully. */
const EVENT_META: Record<string, { label: string; tone: EvidenceItem['tone'] }> = {
  // gateway
  REQUEST_ACCEPTED: { label: 'Gateway accepted the payout request', tone: 'ok' },
  HANDOFF_TO_BANK: { label: 'Gateway forwarded the request to the bank', tone: 'ok' },
  VALIDATION_FAILED: { label: 'Gateway rejected the request during validation', tone: 'bad' },
  REQUEST_REJECTED: { label: 'Gateway rejected the request', tone: 'bad' },
  // bank
  REQUEST_SENT: { label: 'Settlement instruction sent to the bank', tone: 'ok' },
  ACK_RECEIVED: { label: 'Bank acknowledged the instruction', tone: 'ok' },
  SETTLED: { label: 'Bank settled the funds', tone: 'ok' },
  NO_RESPONSE: { label: 'Bank acknowledgement missing', tone: 'warn' },
  PROCESSING_DELAYED: { label: 'Bank reported a processing delay', tone: 'warn' },
  REJECTED: { label: 'Bank rejected the instruction', tone: 'bad' },
  // ledger
  SETTLEMENT_RECORDED: { label: 'Settlement posted to the ledger', tone: 'ok' },
  RECONCILED: { label: 'Ledger reconciled against the bank statement', tone: 'ok' },
  REVERSAL_RECORDED: { label: 'Reversal posted to the ledger', tone: 'bad' },
}

const ABSENCE_LABEL: Record<SystemId, string> = {
  gateway: 'No gateway log found for this transaction',
  bank: 'No bank record received',
  ledger: 'Ledger update never received',
}

function has(rows: LogRow[], event: string): boolean {
  return rows.some((r) => r.event === event)
}

function firstAt(rows: LogRow[], event: string): string | null {
  return rows.find((r) => r.event === event)?.timestamp ?? null
}

/** Build citable evidence for one system, assigning stable ids. */
function buildEvidence(system: SystemId, rows: LogRow[]): EvidenceItem[] {
  if (rows.length === 0) {
    return [
      {
        id: `${system}-no-events`,
        system,
        kind: 'absence',
        timestamp: null,
        event: 'NO_EVENTS',
        code: null,
        detail: ABSENCE_LABEL[system],
        // A missing gateway log is a data gap worth flagging; a missing ledger
        // entry on a stalled transaction is simply the expected consequence.
        tone: system === 'gateway' ? 'warn' : 'neutral',
        label: ABSENCE_LABEL[system],
      },
    ]
  }
  const seen = new Map<string, number>()
  return rows.map((r) => {
    const base = `${system}-${kebab(r.event)}`
    const n = (seen.get(base) ?? 0) + 1
    seen.set(base, n)
    const meta = EVENT_META[r.event]
    return {
      id: n === 1 ? base : `${base}-${n}`,
      system,
      kind: 'event' as const,
      timestamp: r.timestamp,
      event: r.event,
      code: r.code,
      detail: r.detail,
      tone: meta?.tone ?? 'neutral',
      label: meta?.label ?? `${SYSTEM_LABEL[system]}: ${r.event.replace(/_/g, ' ').toLowerCase()}`,
    }
  })
}

// ---------------------------------------------------------------------------
// Node state machines -- one small, explicit function per system.
// ---------------------------------------------------------------------------

function gatewayState(rows: LogRow[]): NodeState {
  if (rows.length === 0) return 'UNKNOWN'
  if (has(rows, 'VALIDATION_FAILED') || has(rows, 'REQUEST_REJECTED')) return 'FAILED'
  if (has(rows, 'HANDOFF_TO_BANK') || has(rows, 'REQUEST_ACCEPTED')) return 'SUCCESS'
  return 'UNKNOWN'
}

function bankState(rows: LogRow[], gwState: NodeState): NodeState {
  // A hard gateway failure means the bank was never contacted -- that is not a
  // bank problem, and the node must read as unavailable rather than failed.
  if (gwState === 'FAILED') return 'UNKNOWN'
  if (rows.length === 0) return 'UNKNOWN'
  if (has(rows, 'SETTLED')) return 'SUCCESS'
  if (has(rows, 'REJECTED')) return 'FAILED'
  if (has(rows, 'PROCESSING_DELAYED')) return 'PROCESSING'
  if (has(rows, 'NO_RESPONSE')) return 'PENDING'
  if (has(rows, 'ACK_RECEIVED') || has(rows, 'REQUEST_SENT')) return 'PROCESSING'
  return 'UNKNOWN'
}

function ledgerState(rows: LogRow[], bkState: NodeState): NodeState {
  if (has(rows, 'REVERSAL_RECORDED')) return 'FAILED'
  if (has(rows, 'RECONCILED') || has(rows, 'SETTLEMENT_RECORDED')) return 'SUCCESS'
  // Only meaningful to call the ledger "pending" once the bank has settled.
  if (bkState === 'SUCCESS') return 'PENDING'
  return 'UNKNOWN'
}

function headlineFor(system: SystemId, state: NodeState, rows: LogRow[]): string {
  const last = rows[rows.length - 1]
  if (last) {
    const meta = EVENT_META[last.event]
    if (meta) return meta.label
    return last.detail || last.event
  }
  if (state === 'PENDING') return ABSENCE_LABEL[system]
  if (system === 'gateway') return ABSENCE_LABEL.gateway
  return 'No events recorded'
}

// ---------------------------------------------------------------------------
// Money state -- derived, never AI-generated.
// ---------------------------------------------------------------------------

function deriveMoney(
  record: TransactionRecord,
  gw: LogRow[],
  bk: LogRow[],
  lg: LogRow[],
  status: TxnStatus,
): MoneyState {
  const reversed = has(lg, 'REVERSAL_RECORDED')
  const gatewayFailed = has(gw, 'VALIDATION_FAILED') || has(gw, 'REQUEST_REJECTED')
  const settled = has(bk, 'SETTLED')

  // Funds are ring-fenced against the source balance the moment the gateway
  // accepts, which is why this can be true while settlement is still open.
  const customerCharged = has(gw, 'REQUEST_ACCEPTED') && !gatewayFailed && !reversed
  const bankAuthorized = has(bk, 'ACK_RECEIVED') || settled
  const settlementCompleted = settled && !reversed
  const ledgerUpdated = has(lg, 'SETTLEMENT_RECORDED') || has(lg, 'RECONCILED')

  let headline: string
  if (reversed) {
    headline = 'The amount was reversed and returned to the source balance. No further action is needed on the money.'
  } else if (settlementCompleted && ledgerUpdated) {
    headline = 'Settlement completed and posted to the ledger.'
  } else if (settlementCompleted) {
    headline = 'Funds have settled at the bank but the ledger has not posted yet. Do not re-initiate this payout.'
  } else if (gatewayFailed) {
    headline = 'No money moved. The request was rejected before it reached the bank.'
  } else if (status === 'UNKNOWN') {
    headline = 'Money movement cannot be confirmed from the available records.'
  } else {
    headline = 'Money has not been settled yet. Avoid initiating a duplicate transaction.'
  }

  return {
    amountPaise: record.amountPaise,
    currency: record.currency,
    customerCharged,
    bankAuthorized,
    settlementCompleted,
    ledgerUpdated,
    reversed,
    headline,
  }
}

// ---------------------------------------------------------------------------
// Incident signature -- the cohort key for "have we seen this before?"
// ---------------------------------------------------------------------------

function deriveIncident(
  gw: LogRow[],
  bk: LogRow[],
  lg: LogRow[],
  now: number,
): Incident {
  // A missing gateway log is itself the defining anomaly: we cannot see the
  // start of the transaction, so it cannot be grouped with complete traces.
  if (gw.length === 0) return { system: 'gateway', marker: 'MISSING_GATEWAY_LOG' }

  // Earliest anomaly across all three logs, by timestamp.
  const candidates: { system: SystemId; row: LogRow }[] = []
  for (const [system, rows] of [
    ['gateway', gw],
    ['bank', bk],
    ['ledger', lg],
  ] as [SystemId, LogRow[]][]) {
    for (const row of rows) {
      if (ANOMALY_EVENTS.has(row.event)) candidates.push({ system, row })
    }
  }
  candidates.sort((a, b) => a.row.timestamp.localeCompare(b.row.timestamp))
  const first = candidates[0]
  if (first) return { system: first.system, marker: first.row.event }

  // No explicit anomaly: the remaining failure mode is a lagging ledger.
  const settledAt = firstAt(bk, 'SETTLED')
  if (settledAt) {
    const postedAt = firstAt(lg, 'SETTLEMENT_RECORDED')
    const lag = (postedAt ? Date.parse(postedAt) : now) - Date.parse(settledAt)
    if (lag > LEDGER_LAG_THRESHOLD_MS) return { system: 'ledger', marker: 'LEDGER_LAG' }
  }
  return { system: null, marker: 'NONE' }
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/**
 * Correlate all three logs for `id` into a single analysed object.
 * Returns null only when the id appears in no log and no manifest entry.
 */
export function traceTransaction(id: string, now = Date.now()): TracedTransaction | null {
  const gw = getLogs('gateway', id)
  const bk = getLogs('bank', id)
  const lg = getLogs('ledger', id)
  const manifest = getTransactionRecord(id)

  if (!manifest && gw.length === 0 && bk.length === 0 && lg.length === 0) return null

  const allRows = [...gw, ...bk, ...lg].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  const firstRow = allRows[0]

  // A log-only transaction still gets a usable record rather than crashing.
  const record: TransactionRecord = manifest ?? {
    id,
    amountPaise: 0,
    currency: 'INR',
    merchantId: 'UNKNOWN',
    merchantName: 'Unknown merchant',
    method: 'UNKNOWN',
    createdAt: firstRow?.timestamp ?? new Date(now).toISOString(),
    scenario: 'UNRECORDED',
  }

  const gwState = gatewayState(gw)
  const bkState = bankState(bk, gwState)
  const lgState = ledgerState(lg, bkState)
  const states: Record<SystemId, NodeState> = { gateway: gwState, bank: bkState, ledger: lgState }
  const rowsBySystem: Record<SystemId, LogRow[]> = { gateway: gw, bank: bk, ledger: lg }

  // Overall status.
  let status: TxnStatus
  if (has(lg, 'REVERSAL_RECORDED')) status = 'REVERSED'
  else if (lgState === 'SUCCESS') status = 'SETTLED'
  else if (gwState === 'FAILED' || bkState === 'FAILED') status = 'FAILED'
  else if (gw.length === 0) status = 'UNKNOWN'
  else status = 'PENDING'

  const systems: SystemTrace[] = SYSTEM_ORDER.map((system) => {
    const rows = rowsBySystem[system]
    const state = states[system]
    return {
      system,
      state,
      // "Reached" means the transaction actually produced activity here.
      reached: rows.length > 0,
      headline: headlineFor(system, state, rows),
      lastAt: rows[rows.length - 1]?.timestamp ?? null,
      events: buildEvidence(system, rows),
    }
  })

  const evidence: EvidenceItem[] = systems.flatMap((s) => s.events)

  // Where forward progress halted.
  let stoppedAt: SystemId | null = null
  if (status !== 'SETTLED') {
    if (gwState === 'FAILED' || gw.length === 0) stoppedAt = 'gateway'
    else if (bkState !== 'SUCCESS') stoppedAt = 'bank'
    else stoppedAt = 'ledger'
  }

  // Only codes that literally appear in the logs, and only on a real failure.
  const failureRow = [...gw, ...bk].find(
    (r) => ANOMALY_EVENTS.has(r.event) && r.code !== null && r.code !== '' && r.code !== 'GW_OK',
  )
  const definitiveFailureCode = failureRow?.code ?? null

  const incident = deriveIncident(gw, bk, lg, now)
  const money = deriveMoney(record, gw, bk, lg, status)
  const missingLogs = SYSTEM_ORDER.filter((s) => rowsBySystem[s].length === 0)

  const lastEventAt = allRows[allRows.length - 1]?.timestamp ?? null
  const createdMs = Date.parse(record.createdAt)
  const ageMs = Number.isFinite(createdMs) ? now - createdMs : 0
  const terminal = status === 'SETTLED' || status === 'FAILED' || status === 'REVERSED'
  const pendingForMs = terminal || !lastEventAt ? null : now - Date.parse(lastEventAt)

  // Evidence strength gates how strongly the AI is permitted to conclude.
  let evidenceStrength: EvidenceStrength
  const eventCount = allRows.length
  if (gw.length === 0 || eventCount <= 1) evidenceStrength = 'WEAK'
  else if (definitiveFailureCode !== null || status === 'SETTLED' || status === 'REVERSED') evidenceStrength = 'STRONG'
  else evidenceStrength = 'MODERATE'

  return {
    record,
    status,
    systems,
    stoppedAt,
    incident,
    signature: `${incident.system ?? 'none'}:${incident.marker}`,
    definitiveFailureCode,
    evidence,
    money,
    evidenceStrength,
    missingLogs,
    lastEventAt,
    ageMs,
    pendingForMs,
  }
}
