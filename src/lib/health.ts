// Per-system health, derived from the mock dataset.
//
// These describe the *simulated* systems in the demo dataset only. Nothing here
// contacts a real gateway, bank, or ledger.

import { corpus } from './similar'
import type { SystemHealth, SystemId } from '../types'

/** Only recent activity should influence the indicator. */
const WINDOW_MS = 6 * 60 * 60 * 1000

const DELAYED_RATIO = 0.15
const DEGRADED_RATIO = 0.3

export function systemHealth(now = Date.now()): SystemHealth[] {
  const recent = corpus(now).filter((t) => now - Date.parse(t.record.createdAt) <= WINDOW_MS)

  const out: SystemHealth[] = []
  for (const system of ['gateway', 'bank', 'ledger'] as SystemId[]) {
    // Only count transactions that actually exercised this system.
    const touched = recent.filter((t) => t.systems.some((s) => s.system === system && s.reached))
    const troubled = touched.filter((t) => {
      const node = t.systems.find((s) => s.system === system)
      return node?.state === 'FAILED' || node?.state === 'PENDING' || node?.state === 'PROCESSING'
    })

    if (touched.length === 0) {
      out.push({ system, state: 'OPERATIONAL', detail: 'No recent activity in the demo dataset' })
      continue
    }

    const ratio = troubled.length / touched.length
    const state = ratio >= DEGRADED_RATIO ? 'DEGRADED' : ratio >= DELAYED_RATIO ? 'DELAYED' : 'OPERATIONAL'
    out.push({
      system,
      state,
      detail:
        troubled.length === 0
          ? `${touched.length} recent transactions, none stalled`
          : `${troubled.length} of ${touched.length} recent transactions stalled or failed`,
    })
  }
  return out
}
