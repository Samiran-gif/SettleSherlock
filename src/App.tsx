import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { DEMO_TRANSACTION_IDS, ingestBackendTransaction } from './lib/dataset'
import { BackendUnavailableError, fetchBackendTransaction } from './lib/backend'
import { traceTransaction } from './lib/trace'
import { findSimilar } from './lib/similar'
import { systemHealth } from './lib/health'
import { ruleBasedDiagnosis } from './lib/rulesDiagnosis'
import { cachedDiagnosis, fetchAiStatus, investigate } from './lib/aiClient'
import { cacheSize, clearCache } from './lib/cache'
import { formatCurrency, formatDateTime } from './lib/format'

import { TransactionTimeline } from './components/TransactionTimeline'
import { AIInsightCard } from './components/AIInsightCard'
import { EvidenceDrawer, type EvidenceRequest } from './components/EvidenceDrawer'
import { MoneyStatusCard } from './components/MoneyStatusCard'
import { RecommendedAction, buildEscalationNote } from './components/RecommendedAction'
import { SimilarTransactions } from './components/SimilarTransactions'
import { RawEvents } from './components/RawEvents'
import { SystemHealth } from './components/SystemHealth'
import { CommandBar } from './components/CommandBar'
import { StatusBadge } from './components/primitives'

import type { AiStatus } from './lib/aiClient'
import type { Diagnosis } from './types'

const DEFAULT_ID = 'TXN_BANK_TIMEOUT_001'

/**
 * Why a lookup produced no transaction.
 *
 * `missing` is a fact about the payment ("the logs do not contain this ID").
 * `unavailable` is a fact about our tooling ("we could not ask"). Rendering
 * both as "transaction not found" told the user the money was untraceable
 * whenever the backend happened to be down.
 */
type SearchError =
  | { kind: 'missing'; id: string }
  | { kind: 'unavailable'; id: string; detail: string }

/** Short labels for the demo switcher. */
const DEMO_LABEL: Record<string, string> = {
  TXN_SUCCESS_001: 'Success',
  TXN_BANK_TIMEOUT_001: 'Bank timeout',
  TXN_PROCESSOR_DELAY_001: 'Processor delay',
  TXN_LEDGER_DELAY_001: 'Ledger delay',
  TXN_GATEWAY_FAILED_001: 'Gateway failed',
  TXN_UNKNOWN_001: 'Incomplete evidence',
}

export default function App() {
  const [txnId, setTxnId] = useState(DEFAULT_ID)
  const [query, setQuery] = useState('')
  const [searchError, setSearchError] = useState<SearchError | null>(null)

  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null)
  const [loading, setLoading] = useState(false)
  const [fromCache, setFromCache] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null)

  const [evidenceRequest, setEvidenceRequest] = useState<EvidenceRequest | null>(null)
  const [rawOpen, setRawOpen] = useState(false)
  const [similarExpanded, setSimilarExpanded] = useState(false)
  const [monitored, setMonitored] = useState<Set<string>>(new Set())
  const [escalation, setEscalation] = useState<string | null>(null)
  const [dark, setDark] = useState(false)
  const [cacheCount, setCacheCount] = useState(0)
  const [animationKey, setAnimationKey] = useState(0)

  const similarRef = useRef<HTMLElement>(null)

  // ---- derived, all deterministic -----------------------------------------
  const txn = useMemo(() => traceTransaction(txnId), [txnId])
  const similar = useMemo(() => (txn ? findSimilar(txn) : null), [txn])
  const health = useMemo(() => systemHealth(), [])

  const cohort = useMemo(
    () =>
      similar
        ? { total: similar.total, settled: similar.settled, reversed: similar.reversed, failed: similar.failed }
        : { total: 0, settled: 0, reversed: 0, failed: 0 },
    [similar],
  )

  // Availability is a single cheap GET; it never analyses anything.
  useEffect(() => {
    void fetchAiStatus().then(setAiStatus)
    setCacheCount(cacheSize())
  }, [])

  /**
   * On transaction change: show the free rule-based diagnosis immediately, or a
   * cached model answer if we already have one. No network call is made here --
   * the model runs only when the user asks for it.
   */
  useEffect(() => {
    if (!txn) {
      setDiagnosis(null)
      return
    }
    const hit = cachedDiagnosis(txn, cohort)
    setDiagnosis(hit ?? ruleBasedDiagnosis(txn))
    setFromCache(hit !== null)
    setAiError(null)
    setSimilarExpanded(false)
    setRawOpen(false)
    setAnimationKey((k) => k + 1)
  }, [txn, cohort])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
  }, [dark])

  const runInvestigation = useCallback(
    async (force = false) => {
      if (!txn || loading) return
      setLoading(true)
      setAiError(null)
      const result = await investigate(txn, cohort, { force })
      setDiagnosis(result.diagnosis)
      setFromCache(result.fromCache)
      setAiError(result.aiError)
      setCacheCount(cacheSize())
      setLoading(false)
    },
    [txn, cohort, loading],
  )

  const onSearch = async (e: React.FormEvent) => {
    e.preventDefault()

    const id = query.trim().toUpperCase()
    if (id === '') return

    setSearchError(null)

    // First check the existing local/demo dataset.
    if (traceTransaction(id)) {
      setTxnId(id)
      setQuery('')
      return
    }

    // Then query the real SettleSherlock backend.
    try {
      const backendTransaction = await fetchBackendTransaction(id)

      if (!backendTransaction) {
        setSearchError({ kind: 'missing', id })
        return
      }

      // Inject the backend transaction into the existing tracing engine.
      ingestBackendTransaction(backendTransaction)

      const traced = traceTransaction(id)

      if (!traced) {
        // The record exists upstream but we could not map it into a trace --
        // a schema mismatch on our side, not a missing payment.
        console.error('[SettleSherlock] Backend record could not be traced:', id)
        setSearchError({
          kind: 'unavailable',
          id,
          detail: 'The backend returned records we could not read. Check the response schema.',
        })
        return
      }

      setTxnId(id)
      setQuery('')
    } catch (error) {
      console.error('[SettleSherlock] Backend lookup failed:', id, error)

      if (error instanceof BackendUnavailableError) {
        setSearchError({ kind: 'unavailable', id, detail: error.message })
        return
      }

      setSearchError({
        kind: 'unavailable',
        id,
        detail: error instanceof Error ? error.message : String(error),
      })
    }
  }

  const isMonitored = monitored.has(txnId)

  return (
    <div className="min-h-screen bg-bg">
      {/* Demo provenance banner -- always visible, never dismissible. */}
      <div className="border-b border-warning/30 bg-warning/10">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-3 gap-y-1 px-4 py-1.5 sm:px-6">
          <span className="chip border-warning/50 bg-warning/10 font-semibold text-warning">
            Demo environment
          </span>
          <p className="text-xs text-ink-2">
            All transactions, gateway, bank and ledger records are <strong>simulated mock data</strong>. No
            real payment system was contacted.
          </p>
        </div>
      </div>

      <header className="border-b border-line bg-surface">
        <div className="mx-auto max-w-[1400px] px-4 py-3 sm:px-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-white">
                <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M4 17l5-5 4 3 7-8" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <div>
                <h1 className="text-sm font-semibold leading-tight text-ink">Settlement Investigator</h1>
                <p className="text-2xs text-muted">AI-assisted settlement investigation console</p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-4">
              <SystemHealth health={health} />
              <div className="flex items-center gap-2">
                <span
                  className="chip"
                  title={
                    aiStatus?.available
                      ? `Model configured: ${aiStatus.model}`
                      : aiStatus?.reason ?? 'Checking model availability'
                  }
                >
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${aiStatus?.available ? 'bg-ai' : 'bg-muted'}`}
                    aria-hidden="true"
                  />
                  {aiStatus === null ? 'Checking AI…' : aiStatus.available ? 'AI ready' : 'AI offline'}
                </span>
                <span className="chip" title="Cached analyses reuse a previous result instead of calling the model">
                  {cacheCount} cached
                </span>
                <button
                  type="button"
                  onClick={() => setDark((d) => !d)}
                  className="btn px-2 py-1"
                  aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
                >
                  {dark ? '☀' : '☾'}
                </button>
              </div>
            </div>
          </div>

          {/* Search + demo switcher */}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <form onSubmit={onSearch} className="flex items-center gap-2">
              <label htmlFor="txn-search" className="sr-only">
                Transaction ID
              </label>
              <input
                id="txn-search"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                  setSearchError(null)
                }}
                placeholder="Enter transaction ID…"
                className="w-56 rounded-md border border-line bg-surface px-2.5 py-1.5 font-mono text-xs text-ink placeholder:font-sans placeholder:text-muted"
                autoComplete="off"
              />
                                            <button type="submit" className="btn py-1.5">
                  Trace
                </button>
                
            </form>

            <span className="hidden text-2xs uppercase tracking-wide text-muted sm:inline">Demo cases</span>
            <div className="flex flex-wrap gap-1.5">
              {DEMO_TRANSACTION_IDS.map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => {
                    setTxnId(id)
                    setSearchError(null)
                  }}
                  className={`chip transition-colors ${
                    id === txnId
                      ? 'border-primary bg-primary/10 font-semibold text-primary'
                      : 'hover:border-primary hover:text-primary'
                  }`}
                  aria-current={id === txnId ? 'true' : undefined}
                >
                  {DEMO_LABEL[id] ?? id}
                </button>
              ))}
            </div>
          </div>

          {searchError?.kind === 'missing' && (
            <p className="mt-2 rounded-md border border-failure/40 bg-failure/5 px-3 py-2 text-xs text-failure">
              <strong>Transaction not found.</strong> No records exist for{' '}
              <span className="font-mono">{searchError.id}</span> in the gateway, bank, or ledger logs.
            </p>
          )}

          {searchError?.kind === 'unavailable' && (
            <p className="mt-2 rounded-md border border-warning/40 bg-warning/5 px-3 py-2 text-xs text-warning">
              <strong>Cannot look up transactions right now.</strong> The settlement backend did not
              answer, so <span className="font-mono">{searchError.id}</span> could not be checked —
              this does not mean the transaction is missing. {searchError.detail} The demo cases above
              still work; they are served from local data.
            </p>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-4 py-5 sm:px-6">
        {!txn || !similar ? (
          <div className="card p-8 text-center">
            <p className="text-sm font-semibold text-ink">Transaction not found</p>
            <p className="mt-1 text-xs text-ink-2">Select one of the demo cases above to begin.</p>
          </div>
        ) : (
          <>
            {/* Headline status */}
            <div className="card mb-4 px-4 py-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2.5">
                    <span className="font-mono text-lg font-semibold text-ink">{txn.record.id}</span>
                    <StatusBadge status={txn.status} size="lg" />
                    {isMonitored && <span className="chip border-success/40 text-success">Monitoring</span>}
                  </div>
                  <p className="mt-1 text-xs text-ink-2">
                    {txn.record.merchantName}{' '}
                    <span className="text-muted">({txn.record.merchantId})</span> · {txn.record.method} ·
                    created {formatDateTime(txn.record.createdAt)}
                  </p>
                </div>
                <div className="text-right">
                  <div className="tnum text-2xl font-semibold tracking-tight text-ink">
                    {formatCurrency(txn.record.amountPaise, txn.record.currency)}
                  </div>
                  <button
                    type="button"
                    onClick={() => void runInvestigation(diagnosis?.source === 'ai')}
                    className="btn btn-ai mt-1.5 py-1.5"
                    disabled={loading}
                  >
                    {loading
                      ? 'Analysing…'
                      : diagnosis?.source === 'ai'
                        ? 'Re-run AI investigation'
                        : 'Run AI investigation'}
                  </button>
                </div>
              </div>
            </div>

            <div className="mb-4">
              <CommandBar
                txn={txn}
                similar={similar}
                diagnosis={diagnosis}
                onInvestigate={() => void runInvestigation(false)}
                investigating={loading}
              />
            </div>

            <div className="mb-4">
              <TransactionTimeline txn={txn} animationKey={`${txnId}-${animationKey}`} />
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
              <div className="space-y-4 lg:col-span-2">
                <AIInsightCard
                  txn={txn}
                  diagnosis={diagnosis}
                  loading={loading}
                  fromCache={fromCache}
                  aiError={aiError}
                  onExplain={setEvidenceRequest}
                  onAnalyze={() => void runInvestigation(false)}
                  canAnalyze={!loading}
                />
                <RecommendedAction
                  txn={txn}
                  diagnosis={diagnosis}
                  monitored={isMonitored}
                  similarCount={similar.total}
                  onMonitor={() =>
                    setMonitored((prev) => {
                      const next = new Set(prev)
                      if (next.has(txnId)) next.delete(txnId)
                      else next.add(txnId)
                      return next
                    })
                  }
                  onEscalate={() => setEscalation(buildEscalationNote(txn, diagnosis))}
                  onViewSimilar={() => {
                    setSimilarExpanded(true)
                    similarRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                  }}
                />
                <RawEvents txn={txn} open={rawOpen} onToggle={() => setRawOpen((v) => !v)} />
              </div>

              <div className="space-y-4">
                <MoneyStatusCard txn={txn} aiSummary={diagnosis?.moneySummary ?? null} />
                <SimilarTransactions
                  ref={similarRef}
                  summary={similar}
                  expanded={similarExpanded}
                  onToggle={() => setSimilarExpanded((v) => !v)}
                />
                <div className="card px-4 py-3">
                  <h2 className="section-title">Cost controls</h2>
                  <ul className="mt-2 space-y-1 text-2xs leading-relaxed text-ink-2">
                    <li>· The model runs only when you ask it to.</li>
                    <li>· Results are cached by transaction ID + data hash.</li>
                    <li>· Status, money, statistics and search are computed locally.</li>
                  </ul>
                  <button
                    type="button"
                    onClick={() => {
                      clearCache()
                      setCacheCount(0)
                      if (txn) setDiagnosis(ruleBasedDiagnosis(txn))
                      setFromCache(false)
                    }}
                    className="btn mt-2.5 py-1 text-xs"
                  >
                    Clear analysis cache ({cacheCount})
                  </button>
                </div>
              </div>
            </div>
          </>
        )}
      </main>

      {txn && (
        <EvidenceDrawer request={evidenceRequest} txn={txn} onClose={() => setEvidenceRequest(null)} />
      )}

      {escalation && <EscalationDialog note={escalation} onClose={() => setEscalation(null)} />}
    </div>
  )
}

/** Escalation note drafted from the trace. No AI call. */
function EscalationDialog({ note, onClose }: { note: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="overlay-in absolute inset-0 bg-ink/40" onClick={onClose} aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="escalation-title"
        className="fade-rise relative flex max-h-[85vh] w-full max-w-lg flex-col rounded-lg border border-line bg-surface shadow-pop"
      >
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 id="escalation-title" className="text-sm font-semibold text-ink">
            Escalation note
          </h2>
          <button ref={closeRef} onClick={onClose} className="btn px-2 py-1" aria-label="Close">
            ✕
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <p className="mb-2 text-xs text-ink-2">
            Drafted from this transaction’s records. Copy it into your ticketing system.
          </p>
          <pre className="max-h-72 overflow-auto rounded-md border border-line bg-bg p-3 font-mono text-2xs leading-relaxed text-ink-2">
            {note}
          </pre>
        </div>
        <footer className="flex justify-end gap-2 border-t border-line px-4 py-3">
          <button onClick={onClose} className="btn">
            Close
          </button>
          <button
            className="btn btn-primary"
            onClick={() => {
              void navigator.clipboard?.writeText(note).then(
                () => {
                  setCopied(true)
                  setTimeout(() => setCopied(false), 1800)
                },
                () => setCopied(false),
              )
            }}
          >
            {copied ? 'Copied' : 'Copy note'}
          </button>
                </footer>
      </div>
    </div>
  )
}


