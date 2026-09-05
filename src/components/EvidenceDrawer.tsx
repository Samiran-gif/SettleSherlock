// Evidence drawer opened by the "Why?" control on every AI claim.
//
// Deliberately separates the AI's conclusion from the raw records that support
// it: the conclusion sits in a labelled block at the top, the evidence below is
// verbatim log data. No reasoning trace is shown -- only citable facts.

import { useEffect, useRef } from 'react'
import { SYSTEM_LABEL, SYSTEM_ORDER } from '../lib/trace'
import { formatDateTime } from '../lib/format'
import { ToneGlyph } from './primitives'
import type { CausalClaim, SystemId, TracedTransaction } from '../types'

export interface EvidenceRequest {
  /** The claim being justified. */
  claim: CausalClaim
  /** Heading shown above the conclusion, e.g. "Likely cause". */
  kind: string
}

export function EvidenceDrawer({
  request,
  txn,
  onClose,
}: {
  request: EvidenceRequest | null
  txn: TracedTransaction
  onClose: () => void
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const restoreTo = useRef<HTMLElement | null>(null)

  // Escape closes; focus moves in on open and returns to the trigger on close.
  useEffect(() => {
    if (!request) return
    restoreTo.current = document.activeElement as HTMLElement | null
    closeRef.current?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        return
      }
      // Minimal focus containment so Tab cannot wander behind the overlay.
      if (e.key === 'Tab' && panelRef.current) {
        const focusables = panelRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        )
        if (focusables.length === 0) return
        const first = focusables[0]
        const last = focusables[focusables.length - 1]
        if (!first || !last) return
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      restoreTo.current?.focus?.()
    }
  }, [request, onClose])

  if (!request) return null

  const cited = new Set(request.claim.evidenceIds)

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="presentation">
      <div
        className="overlay-in absolute inset-0 bg-ink/40 backdrop-blur-[1px]"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="evidence-title"
        className="drawer-in relative flex h-full w-full flex-col bg-surface shadow-pop sm:w-[30rem]"
      >
        <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div>
            <h2 id="evidence-title" className="text-sm font-semibold text-ink">
              AI reasoning evidence
            </h2>
            <p className="mt-0.5 text-xs text-muted">
              Records for {txn.record.id} · Mock transaction data
            </p>
          </div>
          <button ref={closeRef} onClick={onClose} className="btn px-2 py-1" aria-label="Close evidence panel">
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {/* The AI's conclusion, explicitly labelled as such. */}
          <section className="rounded-lg border border-ai-line bg-ai-bg p-4">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-ai">
              {request.kind} — AI conclusion
            </h3>
            <p className="mt-1.5 text-sm font-medium leading-snug text-ink">{request.claim.text}</p>
            <p className="mt-2 text-xs text-ink-2">
              Confidence {request.claim.confidence}% · based on {request.claim.evidenceIds.length}{' '}
              {request.claim.evidenceIds.length === 1 ? 'record' : 'records'}
            </p>
          </section>

          <h3 className="mt-6 text-[11px] font-semibold uppercase tracking-wider text-ink-2">
            Supporting records
          </h3>
          <p className="mt-1 text-xs text-muted">
            Highlighted rows are the ones this conclusion cites.
          </p>

          <div className="mt-3 space-y-5">
            {SYSTEM_ORDER.map((system: SystemId) => {
              const node = txn.systems.find((s) => s.system === system)
              if (!node) return null
              return (
                <section key={system}>
                  <div className="flex items-baseline justify-between border-b border-line pb-1.5">
                    <h4 className="text-xs font-semibold text-ink">{SYSTEM_LABEL[system]}</h4>
                    <span className="text-2xs uppercase tracking-wide text-muted">{node.state}</span>
                  </div>
                  <ul className="mt-2 space-y-1.5">
                    {node.events.map((e) => {
                      const isCited = cited.has(e.id)
                      return (
                        <li
                          key={e.id}
                          className={`rounded-md border px-2.5 py-2 text-xs ${
                            isCited
                              ? 'border-ai-line bg-ai-bg'
                              : 'border-transparent bg-bg'
                          }`}
                        >
                          <div className="flex items-start gap-2">
                            <ToneGlyph tone={e.tone} />
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-baseline gap-x-2">
                                <span className="tnum font-mono text-2xs text-muted">
                                  {e.timestamp ? formatDateTime(e.timestamp) : '—'}
                                </span>
                                <span className="font-mono text-2xs font-semibold text-ink">{e.event}</span>
                                {e.code && (
                                  <span className="font-mono text-2xs text-warning">{e.code}</span>
                                )}
                                {isCited && (
                                  <span className="text-2xs font-semibold uppercase text-ai">cited</span>
                                )}
                              </div>
                              <p className="mt-0.5 leading-snug text-ink-2">{e.detail || e.label}</p>
                            </div>
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                </section>
              )
            })}
          </div>
        </div>

        <footer className="border-t border-line px-5 py-3 text-2xs text-muted">
          Evidence is read directly from the demo gateway, bank and ledger logs. No external
          system was contacted.
        </footer>
      </div>
    </div>
  )
}
