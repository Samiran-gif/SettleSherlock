// Ask-anything bar.
//
// Questions are routed locally first (see lib/commands.ts). Only a causal
// question runs the model, and only on submit -- never on keystroke.

import { useState } from 'react'
import { EXAMPLE_QUESTIONS, routeQuestion } from '../lib/commands'
import type { CommandAnswer } from '../lib/commands'
import type { Diagnosis, SimilarSummary, TracedTransaction } from '../types'

export function CommandBar({
  txn,
  similar,
  diagnosis,
  onInvestigate,
  investigating,
}: {
  txn: TracedTransaction
  similar: SimilarSummary
  diagnosis: Diagnosis | null
  onInvestigate: () => void
  investigating: boolean
}) {
  const [value, setValue] = useState('')
  const [answer, setAnswer] = useState<CommandAnswer | null>(null)

  const ask = (question: string) => {
    const result = routeQuestion(question, txn, similar, diagnosis)
    setAnswer(result)
    // A causal question is the only path that can spend credit.
    if (result.kind === 'ai') onInvestigate()
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    ask(value)
  }

  return (
    <div className="card">
      <form onSubmit={submit} className="flex items-center gap-2 px-3 py-2.5">
        <svg className="h-4 w-4 shrink-0 text-ai" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3z"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
        </svg>
        <label htmlFor="command-input" className="sr-only">
          Ask anything about this transaction
        </label>
        <input
          id="command-input"
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Ask anything about this transaction…"
          className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-muted focus:outline-none"
          autoComplete="off"
        />
        <button type="submit" className="btn btn-primary shrink-0 py-1.5" disabled={investigating}>
          Ask
        </button>
      </form>

      <div className="flex flex-wrap gap-1.5 border-t border-line px-3 py-2">
        {EXAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => {
              setValue(q)
              ask(q)
            }}
            className="chip transition-colors hover:border-primary hover:text-primary"
          >
            {q}
          </button>
        ))}
      </div>

      {answer && answer.kind !== 'ai' && (
        <div className="fade-rise border-t border-line px-4 py-3" aria-live="polite">
          <div className="flex items-center gap-2">
            <h3 className="text-xs font-semibold text-ink">{answer.title}</h3>
            <span className="chip border-success/40 text-success">Instant · no API call</span>
          </div>
          {answer.lines.length > 0 && (
            <ul className="mt-2 space-y-1">
              {answer.lines.map((line, i) => (
                <li key={i} className="text-xs leading-relaxed text-ink-2">
                  {line}
                </li>
              ))}
            </ul>
          )}
          {answer.provenance && <p className="mt-2 text-2xs text-muted">{answer.provenance}</p>}
        </div>
      )}

      {answer && answer.kind === 'ai' && (
        <div className="border-t border-line px-4 py-3" aria-live="polite">
          <p className="text-xs text-ink-2">
            {investigating
              ? 'Running the AI investigation — see the AI insight card below.'
              : 'Answered in the AI insight card below.'}
          </p>
        </div>
      )}
    </div>
  )
}
