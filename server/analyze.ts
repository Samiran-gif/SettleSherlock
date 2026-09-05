// Server-side bridge to the real SettleSherlock backend.
//
// The browser never talks directly to the Python backend.
// React -> /api/analyze -> this bridge -> FastAPI/SettleSherlock.

const BACKEND_URL =
  process.env.SETTLESHERLOCK_BACKEND_URL?.trim() ||
  'http://127.0.0.1:8011'

const MODEL = 'SettleSherlock investigation engine'

const REQUEST_TIMEOUT_MS = 10000

export interface AiRouteResult {
  status: number
  body: unknown
}

interface BackendInvestigation {
  transaction_id: string
  status: string
  root_cause: string
  investigation_confidence: number
  evidence: string[]
  exceptions: string[]
  recommended_action: string
}

interface BackendTransaction {
  transaction_id: string
  gateway: {
    transaction_id: string
    amount: number
    status: string
    timestamp: string
    reference: string
  }
  bank: {
    transaction_id: string
    amount: number
    status: string
    timestamp: string
    reference: string
  }
  ledger: {
    transaction_id: string
    amount: number
    status: string
    timestamp: string
    reference: string
  }
}

interface FrontendEvidence {
  id: string
  system: string
  kind: string
  event: string
  code: string | null
  detail: string
  tOffsetSec?: number
}

interface FrontendPayload {
  id: string
  amountPaise: number
  evidenceStrength: string
  definitiveFailureCode: string | null
  money: {
    customerCharged: boolean
    bankAuthorized: boolean
    settlementCompleted: boolean
    ledgerUpdated: boolean
    reversed: boolean
  }
  evidence: FrontendEvidence[]
}

export interface AiStatusResult {
  available: boolean
  reason: string
  model: string
}

/** A probe is cheap but the badge re-mounts often; a short TTL is plenty. */
const STATUS_PROBE_TIMEOUT_MS = 2000
const STATUS_CACHE_TTL_MS = 5000

let statusCache: { at: number; url: string; value: AiStatusResult } | null = null

/** Test seam: drops the memoised probe result. */
export function resetAiStatusCache(): void {
  statusCache = null
}

/**
 * Reported to the UI as the "AI ready" / "AI offline" badge.
 *
 * This has to actually contact the backend. Returning `available: true` just
 * because a URL is configured made the badge claim readiness while the
 * service was down, which is the one thing the badge exists to tell you.
 */
export async function aiStatus(env: NodeJS.ProcessEnv): Promise<AiStatusResult> {
  const url = env.SETTLESHERLOCK_BACKEND_URL?.trim() || BACKEND_URL

  if (!url) {
    return {
      available: false,
      reason: 'SettleSherlock backend URL is not configured',
      model: MODEL,
    }
  }

  const now = Date.now()
  if (statusCache && statusCache.url === url && now - statusCache.at < STATUS_CACHE_TTL_MS) {
    return statusCache.value
  }

  const value = await probeBackend(url)
  statusCache = { at: now, url, value }
  return value
}

async function probeBackend(url: string): Promise<AiStatusResult> {
  const base = url.replace(/\/+$/, '')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), STATUS_PROBE_TIMEOUT_MS)

  try {
    // Any HTTP reply -- 404 included -- proves something is listening, which is
    // all this check can honestly assert without knowing the health route.
    await fetch(base, { method: 'GET', signal: controller.signal })
    return {
      available: true,
      reason: `Connected to SettleSherlock at ${base}`,
      model: MODEL,
    }
  } catch {
    return {
      available: false,
      reason: controller.signal.aborted
        ? `SettleSherlock at ${base} did not respond within ${STATUS_PROBE_TIMEOUT_MS}ms.`
        : `Cannot reach SettleSherlock at ${base}. Is the service running?`,
      model: MODEL,
    }
  } finally {
    clearTimeout(timer)
  }
}

function normaliseStatus(status: string): string {
  return status.trim().toUpperCase()
}

function moneySummary(payload: FrontendPayload): string {
  const money = payload.money

  if (money.reversed) {
    return 'The transaction was reversed. No successful settlement should be treated as completed.'
  }

  if (money.settlementCompleted) {
    return 'The transaction reached settlement successfully.'
  }

  if (money.bankAuthorized) {
    return 'The bank authorized the transaction, but settlement has not been completed.'
  }

  if (money.customerCharged) {
    return 'The customer was charged, but the transaction has not completed settlement.'
  }

  return 'No completed settlement movement is indicated by the supplied transaction state.'
}

/**
 * Substring test that ignores empty needles.
 *
 * `''.includes` style checks are always true, so an evidence row with no code
 * or no detail used to match every statement mentioning its system -- the
 * citation then pointed at an arbitrary record.
 */
function mentions(haystack: string, needle: string | null): boolean {
  if (needle === null) return false
  const trimmed = needle.trim().toLowerCase()
  if (trimmed === '') return false
  return haystack.includes(trimmed)
}

/**
 * Match backend evidence to genuine frontend evidence IDs.
 *
 * The backend supplies factual statements such as:
 * "Gateway status is FAILED"
 * "Bank status is FAILED"
 * "Ledger status is REVERSED"
 *
 * We only return IDs that actually exist in the frontend trace.
 */
function mapEvidenceIds(
  backendEvidence: string[],
  frontendEvidence: FrontendEvidence[],
): string[] {
  const ids: string[] = []

  for (const statement of backendEvidence) {
    const text = statement.toLowerCase()

    let system: string | null = null

    if (text.includes('gateway')) system = 'gateway'
    else if (text.includes('bank')) system = 'bank'
    else if (text.includes('ledger')) system = 'ledger'

    if (!system) continue

    const match = frontendEvidence.find(
      (e) =>
        e.system === system &&
        (mentions(text, e.event) || mentions(text, e.detail) || mentions(text, e.code)),
    )

    if (match && !ids.includes(match.id)) {
      ids.push(match.id)
      continue
    }

    // If exact wording differs between the Python backend and frontend trace,
    // fall back to the strongest event for that system: one carrying a failure
    // code if there is one, otherwise the most recent.
    const forSystem = frontendEvidence.filter(
      (e) => e.system === system && e.kind === 'event',
    )

    const fallback =
      forSystem.find((e) => e.code !== null && e.code !== '') ??
      forSystem[forSystem.length - 1]

    if (fallback && !ids.includes(fallback.id)) {
      ids.push(fallback.id)
    }
  }

  return ids
}

function buildAlternativeEvidence(
  frontendEvidence: FrontendEvidence[],
): string[] {
  return frontendEvidence
    .filter((e) => e.kind === 'event')
    .slice(0, 3)
    .map((e) => e.id)
}

function buildDiagnosis(
  investigation: BackendInvestigation,
  transaction: BackendTransaction,
  payload: FrontendPayload,
): Record<string, unknown> {
  const backendEvidenceIds = mapEvidenceIds(
    investigation.evidence,
    payload.evidence,
  )

  const status = normaliseStatus(investigation.status)

  const strongEvidence =
    investigation.investigation_confidence >= 80 &&
    backendEvidenceIds.length > 0

  const weakEvidence =
    payload.evidenceStrength === 'WEAK' ||
    !strongEvidence

  const likelyCauseIds = backendEvidenceIds

  const likelyCauseText =
    investigation.root_cause?.trim() ||
    'The SettleSherlock investigation identified an issue requiring review.'

  const amount = transaction.gateway?.amount ?? 0

  const amountText = new Intl.NumberFormat('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount)

  let headline: string

  switch (status) {
    case 'SETTLED':
      headline =
        `Transaction ${payload.id} is settled. ` +
        `Gateway, bank and ledger records are consistent for ₹${amountText}.`
      break

    case 'FAILED':
      headline =
        `Transaction ${payload.id} failed. ${investigation.root_cause}.`
      break

    case 'DELAYED':
      headline =
        `Transaction ${payload.id} is delayed. ` +
        `${investigation.root_cause}.`
      break

    case 'INCOMPLETE':
      headline =
        `Transaction ${payload.id} is incomplete. ` +
        `${investigation.root_cause}.`
      break

    default:
      headline =
        `Transaction ${payload.id} requires investigation. ` +
        `${investigation.root_cause}.`
  }

  const moneyMoved =
    payload.money.customerCharged ||
    payload.money.settlementCompleted

  let retryRecommended = false

  if (!moneyMoved && (status === 'FAILED' || status === 'NEEDS_INVESTIGATION')) {
    retryRecommended = true
  }

  let actionTitle = 'Review settlement records'
  let actionReason =
    investigation.recommended_action ||
    'Review the gateway, bank and ledger records before taking further action.'

  if (status === 'FAILED') {
    actionTitle = moneyMoved
      ? 'Review failure and reconciliation status'
      : 'Review failure and consider retry'

    actionReason =
      investigation.recommended_action ||
      'Review the gateway failure and confirm the downstream reconciliation state.'
  }

  if (status === 'SETTLED') {
    actionTitle = 'No corrective action required'
    actionReason =
      'Gateway, bank and ledger records indicate a completed settlement.'
    retryRecommended = false
  }

  const alternatives: {
    text: string
    confidence: number
    evidenceIds: string[]
  }[] = []

  if (weakEvidence) {
    const ids = buildAlternativeEvidence(payload.evidence)

    alternatives.push(
      {
        text: 'The available settlement evidence may be incomplete and requires further verification.',
        confidence: 45,
        evidenceIds: ids,
      },
      {
        text: 'A downstream processing or reconciliation issue may account for the observed state.',
        confidence: 35,
        evidenceIds: ids,
      },
    )
  }

  return {
    headline,
    likelyCauseText: weakEvidence ? '' : likelyCauseText,
    likelyCauseConfidence: investigation.investigation_confidence,
    likelyCauseEvidenceIds: likelyCauseIds,
    alternatives,
    moneySummary: moneySummary(payload),
    recommendedActionTitle: actionTitle,
    recommendedActionReason: actionReason,
    retryRecommended,
    weakEvidence,
  }
}

async function fetchBackend(
  transactionId: string,
  backendUrl: string,
): Promise<{
  investigation: BackendInvestigation
  transaction: BackendTransaction
}> {
  const controller = new AbortController()

  const timer = setTimeout(
    () => controller.abort(),
    REQUEST_TIMEOUT_MS,
  )

  try {
    const base = backendUrl.replace(/\/+$/, '')

    const investigationResponse = await fetch(
      `${base}/api/v1/transactions/${encodeURIComponent(transactionId)}/investigation`,
      {
        method: 'GET',
        signal: controller.signal,
      },
    )

    if (!investigationResponse.ok) {
      throw new Error(
        `SettleSherlock investigation returned ${investigationResponse.status}`,
      )
    }

    const investigation =
      (await investigationResponse.json()) as BackendInvestigation

    const transactionResponse = await fetch(
      `${base}/api/v1/transactions/${encodeURIComponent(transactionId)}`,
      {
        method: 'GET',
        signal: controller.signal,
      },
    )

    if (!transactionResponse.ok) {
      throw new Error(
        `SettleSherlock transaction lookup returned ${transactionResponse.status}`,
      )
    }

    const transaction =
      (await transactionResponse.json()) as BackendTransaction

    return { investigation, transaction }
  } finally {
    clearTimeout(timer)
  }
}

/**
 * Main /api/analyze bridge.
 *
 * The existing React frontend continues sending its normal payload.
 * We use payload.id to ask SettleSherlock for the authoritative investigation.
 */
export async function runAnalysis(
  payload: unknown,
  env: NodeJS.ProcessEnv,
): Promise<AiRouteResult> {
  if (
    typeof payload !== 'object' ||
    payload === null
  ) {
    return {
      status: 400,
      body: {
        error: 'invalid_payload',
        reason: 'Analysis payload is invalid.',
      },
    }
  }

  const p = payload as FrontendPayload

  if (!p.id || typeof p.id !== 'string') {
    return {
      status: 400,
      body: {
        error: 'missing_transaction_id',
        reason: 'Transaction ID is required.',
      },
    }
  }

  // buildDiagnosis indexes into both of these. Without this check a malformed
  // body threw a TypeError that the catch below reported as "backend
  // unavailable" -- blaming the backend for our own bad request.
  if (!Array.isArray(p.evidence)) {
    return {
      status: 400,
      body: {
        error: 'invalid_evidence',
        reason: 'Analysis payload must include an evidence array.',
      },
    }
  }

  if (typeof p.money !== 'object' || p.money === null) {
    return {
      status: 400,
      body: {
        error: 'invalid_money_state',
        reason: 'Analysis payload must include a money state object.',
      },
    }
  }

  const base =
    env.SETTLESHERLOCK_BACKEND_URL?.trim() ||
    BACKEND_URL

  if (!base) {
    return {
      status: 503,
      body: {
        error: 'backend_unavailable',
        reason: 'SettleSherlock backend URL is not configured.',
      },
    }
  }

  try {
    const result = await fetchBackend(p.id, base)

    const diagnosis = buildDiagnosis(
      result.investigation,
      result.transaction,
      p,
    )

    return {
      status: 200,
      body: {
        diagnosis,
        usage: {
          input: 0,
          output: 0,
        },
        model: MODEL,
        backend: 'SettleSherlock',
        transactionId: p.id,
      },
    }
  } catch (err) {
    const reason =
      err instanceof Error
        ? err.message
        : 'Unable to reach SettleSherlock backend.'

    return {
      status: 503,
      body: {
        error: 'backend_unavailable',
        reason,
      },
    }
  }
}