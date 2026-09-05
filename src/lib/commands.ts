// Intent routing for the command bar.
//
// The point of this module is cost: most support questions are lookups, not
// reasoning. Anything answerable from the trace is answered here, for free and
// instantly. Only a genuine "why did this happen" escalates to the model.

import { formatCurrency, formatDateTime, formatDuration } from './format'
import { SYSTEM_LABEL } from './trace'
import type { Diagnosis, SimilarSummary, TracedTransaction } from '../types'

export interface CommandAnswer {
  /** 'local' answers cost nothing. 'ai' asks the caller to run an investigation. */
  kind: 'local' | 'ai' | 'unknown'
  title: string
  lines: string[]
  /** Explains where the answer came from, shown as a footnote. */
  provenance: string
}

/** Ordered: the first matching rule wins. */
const RULES: { test: RegExp; id: string }[] = [
  { test: /\b(how long|elapsed|pending for|duration|age)\b/i, id: 'duration' },
  { test: /\b(failed|error|errors|problem)\s*(events|logs)?\b/i, id: 'failedEvents' },
  { test: /\b(where.*money|money|funds|amount|charged|debited)\b/i, id: 'money' },
  { test: /\b(similar|before|seen this|history|other transactions)\b/i, id: 'similar' },
  { test: /\b(retry|re-?try|resend|re-?submit|safe to)\b/i, id: 'retry' },
  { test: /\b(raw|all events|timeline|logs|what events)\b/i, id: 'events' },
  { test: /\b(bank and ledger|between .* and|handoff|gateway and bank)\b/i, id: 'between' },
  { test: /\b(status|state|where is it|stuck|stopped)\b/i, id: 'status' },
  // Only genuine causal questions reach the model. Matched on stems so
  // inflections ("caused", "diagnosing", "investigated") route correctly.
  { test: /\b(why|caus|reason|diagnos|investigat|explain|what happened)/i, id: 'why' },
]

export const EXAMPLE_QUESTIONS = [
  'Why did this fail?',
  'Where is the money?',
  'How long has it been pending?',
  'Show me the failed events.',
  'Can I retry this?',
  'Has this happened before?',
]

export function routeQuestion(
  question: string,
  txn: TracedTransaction,
  similar: SimilarSummary,
  diagnosis: Diagnosis | null,
): CommandAnswer {
  const q = question.trim()
  if (q === '') {
    return { kind: 'unknown', title: 'Ask a question', lines: [], provenance: '' }
  }

  const rule = RULES.find((r) => r.test.test(q))
  const local = (title: string, lines: string[]): CommandAnswer => ({
    kind: 'local',
    title,
    lines,
    provenance: 'Computed locally from the transaction records — no AI call.',
  })

  switch (rule?.id) {
    case 'duration': {
      const lines = [
        txn.pendingForMs !== null
          ? `Pending for ${formatDuration(txn.pendingForMs)} since the last recorded event.`
          : `This transaction reached a terminal state (${txn.status.toLowerCase()}), so it is not pending.`,
        `Created ${formatDateTime(txn.record.createdAt)} · age ${formatDuration(txn.ageMs)}.`,
      ]
      if (txn.lastEventAt) lines.push(`Last event at ${formatDateTime(txn.lastEventAt)}.`)
      return local('How long has it been pending?', lines)
    }

    case 'failedEvents': {
      const bad = txn.evidence.filter((e) => e.tone === 'bad' || e.tone === 'warn')
      if (bad.length === 0) {
        return local('Failed events', ['No failed or warning events were recorded for this transaction.'])
      }
      return local(
        'Failed and warning events',
        bad.map(
          (e) =>
            `[${SYSTEM_LABEL[e.system]}] ${e.timestamp ? formatDateTime(e.timestamp) : '—'} · ${e.event}${
              e.code ? ` (${e.code})` : ''
            } — ${e.label}`,
        ),
      )
    }

    case 'money': {
      const m = txn.money
      return local('Where is the money?', [
        `Amount: ${formatCurrency(m.amountPaise, m.currency)}`,
        `Customer charged: ${m.customerCharged ? 'yes' : 'no'}`,
        `Bank authorized: ${m.bankAuthorized ? 'yes' : 'no'}`,
        `Settlement completed: ${m.settlementCompleted ? 'yes' : 'no'}`,
        `Ledger updated: ${m.ledgerUpdated ? 'yes' : 'no'}`,
        m.headline,
      ])
    }

    case 'similar': {
      if (similar.total === 0) {
        return local('Has this happened before?', [
          'No other transaction in the demo dataset shares this incident pattern.',
        ])
      }
      const lines = [`${similar.total} similar transactions share this incident signature.`]
      if (similar.settled > 0) lines.push(`${similar.settled} eventually settled.`)
      if (similar.reversed > 0) lines.push(`${similar.reversed} were reversed.`)
      if (similar.failed > 0) lines.push(`${similar.failed} failed outright.`)
      if (similar.avgResolutionMs !== null) {
        lines.push(`Average resolution time: ${formatDuration(similar.avgResolutionMs)}.`)
      }
      return local('Has this happened before?', lines)
    }

    case 'retry': {
      const moneyMoved = txn.money.settlementCompleted || txn.money.customerCharged
      const lines: string[] = []
      if (txn.money.settlementCompleted) {
        lines.push('No. The funds have already settled at the bank — a retry would duplicate the payout.')
      } else if (txn.money.customerCharged) {
        lines.push('Not yet. Funds are held at source and the original instruction may still complete.')
      } else {
        lines.push('Yes. No money has moved, so a corrected re-submission is safe.')
      }
      if (diagnosis) {
        lines.push(
          `Current recommendation: ${diagnosis.recommendedAction.title} (${
            diagnosis.recommendedAction.retryRecommended ? 'retry safe' : 'do not retry'
          }).`,
        )
      }
      lines.push(`Derived from the money state, not from a model: money moved = ${moneyMoved}.`)
      return local('Can I retry this?', lines)
    }

    case 'events': {
      return local(
        'Recorded events',
        txn.evidence.map(
          (e) =>
            `[${SYSTEM_LABEL[e.system]}] ${e.timestamp ? formatDateTime(e.timestamp) : '—'} · ${e.event}${
              e.code ? ` (${e.code})` : ''
            }`,
        ),
      )
    }

    case 'between': {
      const bank = txn.systems.find((s) => s.system === 'bank')
      const ledger = txn.systems.find((s) => s.system === 'ledger')
      const lines = [
        `Bank: ${bank?.state ?? 'UNKNOWN'} — ${bank?.headline ?? 'no events'}`,
        `Ledger: ${ledger?.state ?? 'UNKNOWN'} — ${ledger?.headline ?? 'no events'}`,
      ]
      if (bank?.lastAt && ledger?.lastAt) {
        const gap = Date.parse(ledger.lastAt) - Date.parse(bank.lastAt)
        lines.push(`Gap between the last bank and ledger events: ${formatDuration(Math.abs(gap))}.`)
      } else if (bank?.lastAt && !ledger?.lastAt) {
        lines.push('The ledger recorded nothing after the bank activity.')
      }
      return local('What happened between the bank and the ledger?', lines)
    }

    case 'status': {
      return local('Current status', [
        `Status: ${txn.status}`,
        txn.stoppedAt ? `Stopped at: ${SYSTEM_LABEL[txn.stoppedAt]}` : 'Completed all three stages.',
        ...txn.systems.map((s) => `${SYSTEM_LABEL[s.system]}: ${s.state} — ${s.headline}`),
      ])
    }

    case 'why':
      return {
        kind: 'ai',
        title: 'Investigating…',
        lines: [],
        provenance: 'This is a causal question, so it runs the AI investigation.',
      }

    default:
      return {
        kind: 'unknown',
        title: 'I can’t answer that from the transaction records',
        lines: [
          'Try one of the example questions, or ask "why did this fail?" to run a full investigation.',
        ],
        provenance: 'No AI call was made.',
      }
  }
}
