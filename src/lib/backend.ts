const BACKEND_URL = '/api/backend'

export interface BackendSystemRecord {
  transaction_id: string
  amount: number
  status: string
  timestamp: string
  reference: string
}

export interface BackendTransaction {
  transaction_id: string
  gateway: BackendSystemRecord | null
  bank: BackendSystemRecord | null
  ledger: BackendSystemRecord | null
}

/**
 * The backend could not be reached at all, or answered with a server fault.
 *
 * This is deliberately a distinct type from "no such transaction": an outage
 * means we know nothing about the ID, whereas a 404 means we know it is absent
 * from the gateway, bank and ledger logs. Conflating the two tells the user
 * their transaction does not exist when in fact nobody looked.
 */
export class BackendUnavailableError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'BackendUnavailableError'
  }
}

export async function fetchBackendTransaction(id: string): Promise<BackendTransaction | null> {
  let response: Response
  try {
    response = await fetch(`${BACKEND_URL}/api/v1/transactions/${encodeURIComponent(id)}`)
  } catch {
    // fetch() rejects only on transport failure -- dev server gone, DNS, CORS.
    throw new BackendUnavailableError(
      'No response from the backend. Is the SettleSherlock service running?',
    )
  }

  if (response.status === 404) {
    return null
  }

  if (response.status >= 500) {
    // The Vite proxy answers 503 + {error:'backend_unreachable'} when the
    // upstream refuses the connection; anything else is a real server fault.
    const detail = await readErrorDetail(response)
    throw new BackendUnavailableError(
      detail ?? `Backend returned HTTP ${response.status}.`,
    )
  }

  if (!response.ok) {
    throw new Error(`Backend rejected the lookup with HTTP ${response.status}.`)
  }

  return (await response.json()) as BackendTransaction
}

/** Best-effort extraction of the proxy/backend error message; never throws. */
async function readErrorDetail(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown; error?: unknown }
    if (typeof body.detail === 'string' && body.detail !== '') return body.detail
    if (typeof body.error === 'string' && body.error !== '') return body.error
    return null
  } catch {
    return null
  }
}
