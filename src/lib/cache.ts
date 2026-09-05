// Two-tier cache for AI diagnoses: in-memory for the session, localStorage so
// a reload does not re-bill for an answer we already have.
//
// Key = transactionId + hash of the relevant transaction data. If the data
// changes the key changes; if it does not, the model is never called again.

import type { Diagnosis } from '../types'

const PREFIX = 'settlement-investigator:diagnosis:v1'

const memory = new Map<string, Diagnosis>()

function keyFor(transactionId: string, dataHash: string): string {
  return `${PREFIX}:${transactionId}:${dataHash}`
}

export function readCache(transactionId: string, dataHash: string): Diagnosis | null {
  const key = keyFor(transactionId, dataHash)

  const hit = memory.get(key)
  if (hit) return hit

  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Diagnosis
    memory.set(key, parsed)
    return parsed
  } catch {
    // A corrupt or unavailable store must never break the console.
    return null
  }
}

export function writeCache(transactionId: string, dataHash: string, diagnosis: Diagnosis): void {
  const key = keyFor(transactionId, dataHash)
  memory.set(key, diagnosis)
  try {
    localStorage.setItem(key, JSON.stringify(diagnosis))
  } catch {
    // Quota or private-mode failures are non-fatal; the memory tier still works.
  }
}

/** Which transactions already have a cached answer -- drives the UI badge. */
export function isCached(transactionId: string, dataHash: string): boolean {
  return readCache(transactionId, dataHash) !== null
}

/** Clears every cached diagnosis. Exposed in the UI for demo resets. */
export function clearCache(): number {
  memory.clear()
  let removed = 0
  try {
    const doomed: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k && k.startsWith(PREFIX)) doomed.push(k)
    }
    for (const k of doomed) {
      localStorage.removeItem(k)
      removed++
    }
  } catch {
    /* ignore */
  }
  return removed
}

/** Count of cached analyses, shown in the cost-control readout. */
export function cacheSize(): number {
  try {
    let n = 0
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k && k.startsWith(PREFIX)) n++
    }
    return n
  } catch {
    return memory.size
  }
}
