// System health for the three simulated systems.
//
// Derived from the demo dataset. These are NOT live production systems and the
// labelling says so explicitly.

import { SYSTEM_LABEL } from '../lib/trace'
import { HEALTH_STYLE } from './primitives'
import type { SystemHealth as SystemHealthType } from '../types'

export function SystemHealth({ health }: { health: SystemHealthType[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
      {health.map((h) => {
        const s = HEALTH_STYLE[h.state]
        return (
          <div key={h.system} className="flex items-center gap-2" title={h.detail}>
            <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} aria-hidden="true" />
            <span className="text-xs text-ink-2">{SYSTEM_LABEL[h.system]}</span>
            <span className={`text-xs font-semibold ${s.cls}`}>{s.label}</span>
          </div>
        )
      })}
    </div>
  )
}
