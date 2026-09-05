// The Gateway -> Bank -> Ledger timeline.
//
// Nodes reveal in sequence with a pulse travelling along each connector, so the
// transaction visibly moves through the infrastructure. When progress halts,
// the pulse stops there and everything downstream renders muted and dashed.
//
// Replaying the animation is done by changing `animationKey`, which remounts
// the list and restarts the CSS animations. No animation library, no JS timers.

import { SYSTEM_LABEL } from '../lib/trace'
import { formatDateTime } from '../lib/format'
import { NODE_STATE_LABEL, NODE_STATE_STYLE, StateIcon } from './primitives'
import type { NodeState, SystemTrace, TracedTransaction } from '../types'

/** A node is "live" once the transaction actually produced activity there. */
function isLive(state: NodeState): boolean {
  return state !== 'UNKNOWN'
}

function stateAnimClass(state: NodeState): string {
  switch (state) {
    case 'SUCCESS':
      return 'state-success'
    case 'PENDING':
      return 'state-pending'
    case 'FAILED':
      return 'state-failed'
    case 'UNKNOWN':
      return 'state-unknown'
    case 'PROCESSING':
      return ''
  }
}

function TransactionNode({ node, index }: { node: SystemTrace; index: number }) {
  const style = NODE_STATE_STYLE[node.state]
  const live = isLive(node.state)

  return (
    <li
      className="node-arrive flex min-w-0 flex-1 flex-row items-start gap-3 sm:flex-col sm:items-center sm:text-center"
      style={{ ['--i' as string]: index }}
    >
      <div
        className={`relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full border-2
                    ${style.ring} ${style.text} ${stateAnimClass(node.state)}`}
      >
        <StateIcon state={node.state} />
      </div>

      <div className="min-w-0 sm:mt-1">
        <div className="flex items-center gap-2 sm:justify-center">
          <span className={`text-sm font-semibold ${live ? 'text-ink' : 'text-muted'}`}>
            {SYSTEM_LABEL[node.system]}
          </span>
          <span className={`text-[11px] font-semibold uppercase tracking-wide ${style.text}`}>
            {NODE_STATE_LABEL[node.state]}
          </span>
        </div>
        <div className="tnum mt-0.5 text-xs text-muted">
          {node.lastAt ? formatDateTime(node.lastAt) : 'No events'}
        </div>
        <p className={`mt-1 max-w-[15rem] text-xs leading-snug ${live ? 'text-ink-2' : 'text-muted'}`}>
          {node.headline}
        </p>
      </div>
    </li>
  )
}

/**
 * Connector between two nodes. `active` means the transaction crossed this
 * hop; inactive connectors are dashed and carry no pulse.
 */
function Connector({ active, index, tone }: { active: boolean; index: number; tone: NodeState }) {
  const colour =
    tone === 'FAILED'
      ? 'bg-failure/50'
      : tone === 'PENDING'
        ? 'bg-warning/50'
        : tone === 'PROCESSING'
          ? 'bg-info/50'
          : 'bg-success/50'

  return (
    <li
      aria-hidden="true"
      className="relative ml-6 h-8 w-0.5 shrink-0 self-stretch sm:ml-0 sm:mt-6 sm:h-0.5 sm:w-auto sm:flex-1"
      style={{ ['--i' as string]: index }}
    >
      {/* Track */}
      <div
        className={`absolute inset-0 rounded-full ${
          active ? 'bg-line-strong' : 'border-l-2 border-dashed border-line-strong sm:border-l-0 sm:border-t-2'
        }`}
      />
      {active && (
        <>
          <div className={`connector-fill absolute inset-0 rounded-full ${colour}`} style={{ ['--i' as string]: index }} />
          {/* The travelling pulse. Horizontal layouts only -- on the stacked
              mobile layout the fill alone reads clearly and costs less paint. */}
          <div
            className="pulse-travel absolute top-1/2 hidden h-2 w-2 -translate-y-1/2 rounded-full bg-white shadow-[0_0_10px_2px_rgb(var(--c-primary)/0.9)] sm:block"
            style={{ ['--i' as string]: index }}
          />
        </>
      )}
    </li>
  )
}

export function TransactionTimeline({
  txn,
  animationKey,
}: {
  txn: TracedTransaction
  animationKey: string | number
}) {
  const nodes = txn.systems

  return (
    <div className="card p-5 sm:p-6">
      <div className="mb-5 flex items-center justify-between gap-3">
        <h2 className="section-title">Settlement path</h2>
        <span className="chip" title="These are simulated systems in the demo dataset">
          Simulated payment flow
        </span>
      </div>

      <ol
        key={animationKey}
        className="flex flex-col sm:flex-row sm:items-start"
        aria-label="Transaction progress through gateway, bank and ledger"
      >
        {nodes.map((node, i) => {
          const prev = nodes[i - 1]
          const items = []
          if (prev) {
            // The hop is active only if the upstream system actually handed off
            // and this system recorded activity.
            const active = isLive(prev.state) && node.events.some((e) => e.kind === 'event')
            items.push(
              <Connector
                key={`c-${node.system}`}
                active={active}
                index={i - 1}
                tone={active ? node.state : 'UNKNOWN'}
              />,
            )
          }
          items.push(<TransactionNode key={node.system} node={node} index={i} />)
          return items
        })}
      </ol>

      {txn.stoppedAt && (
        <p className="mt-5 border-t border-line pt-4 text-xs text-ink-2">
          Progress stopped at{' '}
          <span className="font-semibold text-ink">{SYSTEM_LABEL[txn.stoppedAt]}</span>
          {txn.missingLogs.length > 0 && (
            <>
              {' '}· No records found in{' '}
              {txn.missingLogs.map((s) => SYSTEM_LABEL[s]).join(', ')}
            </>
          )}
        </p>
      )}
    </div>
  )
}
