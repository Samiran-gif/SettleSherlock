// Shared domain types for the settlement investigation console.
//
// Everything in this file describes *derived* data. The AI layer consumes a
// TracedTransaction and returns a Diagnosis; it never produces the states,
// booleans, or statistics, which are all computed deterministically.

export type SystemId = 'gateway' | 'bank' | 'ledger'

/** Visual + semantic state of one system node in the timeline. */
export type NodeState = 'SUCCESS' | 'PROCESSING' | 'PENDING' | 'FAILED' | 'UNKNOWN'

/** Overall lifecycle state of the transaction. */
export type TxnStatus = 'SETTLED' | 'PENDING' | 'FAILED' | 'REVERSED' | 'UNKNOWN'

/** Drives the glyph shown next to a piece of evidence: ok=check warn=! bad=x neutral=circle */
export type EvidenceTone = 'ok' | 'warn' | 'bad' | 'neutral'

export type EvidenceStrength = 'STRONG' | 'MODERATE' | 'WEAK'

/** One parsed row from a gateway/bank/ledger log. */
export interface LogRow {
  transactionId: string
  timestamp: string
  event: string
  code: string | null
  detail: string
}

/** One row from the transaction manifest. */
export interface TransactionRecord {
  id: string
  amountPaise: number
  currency: string
  merchantId: string
  merchantName: string
  method: string
  createdAt: string
  /** Ground-truth generator label. Used for cohort QA only -- never displayed as a finding. */
  scenario: string
}

/**
 * A citable fact about the transaction. `kind: 'absence'` records something
 * that is *missing* (e.g. no ledger event), which matters as much as a present
 * event and must be citable so the AI can reference a gap without inventing one.
 */
export interface EvidenceItem {
  id: string
  system: SystemId
  kind: 'event' | 'absence'
  timestamp: string | null
  event: string
  code: string | null
  detail: string
  tone: EvidenceTone
  /** Short human-readable statement of the fact. */
  label: string
}

export interface SystemTrace {
  system: SystemId
  state: NodeState
  /** False when the transaction never got this far (downstream of a hard stop). */
  reached: boolean
  headline: string
  lastAt: string | null
  events: EvidenceItem[]
}

export interface MoneyState {
  amountPaise: number
  currency: string
  customerCharged: boolean
  bankAuthorized: boolean
  settlementCompleted: boolean
  ledgerUpdated: boolean
  reversed: boolean
  /** Deterministic one-line summary. The AI may restate this but never sets it. */
  headline: string
}

/**
 * The first anomaly encountered along the trace. Used as the cohort key for
 * "have we seen this before?" -- a transaction that timed out and later settled
 * still shares an incident with one that is currently stuck at the same point.
 */
export interface Incident {
  system: SystemId | null
  marker: string
}

export interface TracedTransaction {
  record: TransactionRecord
  status: TxnStatus
  /** Always ordered gateway -> bank -> ledger. */
  systems: SystemTrace[]
  /** The system where forward progress halted, or null if it completed. */
  stoppedAt: SystemId | null
  incident: Incident
  /** `${incident.system}:${incident.marker}` -- the cohort matching key. */
  signature: string
  /** A real failure code from the logs, or null. Never inferred. */
  definitiveFailureCode: string | null
  evidence: EvidenceItem[]
  money: MoneyState
  evidenceStrength: EvidenceStrength
  missingLogs: SystemId[]
  lastEventAt: string | null
  /** Age since creation, in ms. */
  ageMs: number
  /** How long it has been stalled, or null if it reached a terminal state. */
  pendingForMs: number | null
}

// ---------------------------------------------------------------------------
// AI diagnosis
// ---------------------------------------------------------------------------

export interface CausalClaim {
  text: string
  /** 0-100. Must reflect evidence strength. */
  confidence: number
  /** Ids that must exist in TracedTransaction.evidence. Validated on receipt. */
  evidenceIds: string[]
}

export interface RecommendedAction {
  title: string
  reason: string
  retryRecommended: boolean
}

export interface Diagnosis {
  /** 'ai' = model-generated; 'rules' = deterministic fallback. Always shown to the user. */
  source: 'ai' | 'rules'
  /** Plain answer to "what happened?" */
  headline: string
  likelyCause: CausalClaim | null
  /** Populated instead of a single cause when evidence is weak. */
  alternatives: CausalClaim[]
  definitiveFailureCode: string | null
  moneySummary: string
  recommendedAction: RecommendedAction
  /** True when the model could not reach a confident single cause. */
  weakEvidence: boolean
}

// ---------------------------------------------------------------------------
// Cohort statistics
// ---------------------------------------------------------------------------

export interface SimilarPeer {
  id: string
  merchantName: string
  amountPaise: number
  outcome: TxnStatus
  createdAt: string
  /** Time from creation to terminal state, or null if still unresolved. */
  resolutionMs: number | null
}

export interface SimilarSummary {
  signature: string
  total: number
  settled: number
  reversed: number
  failed: number
  unresolved: number
  /** Mean resolution time across peers that reached a terminal state. */
  avgResolutionMs: number | null
  peers: SimilarPeer[]
}

export type HealthState = 'OPERATIONAL' | 'DELAYED' | 'DEGRADED'

export interface SystemHealth {
  system: SystemId
  state: HealthState
  /** Deterministic supporting detail, e.g. "3 of 41 recent events stalled". */
  detail: string
}
