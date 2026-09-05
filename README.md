# Settlement Investigator

An AI-assisted settlement investigation console for fintech support teams.

Enter a transaction ID and immediately see what happened, where it stopped, the likely
cause with confidence and citable evidence, what to do next, where the money is, and
whether it has happened before.

> **Demo environment.** Every transaction, gateway, bank, and ledger record in this
> project is generated mock data. No real payment system is contacted at any point.
> The UI states this permanently in a banner that cannot be dismissed.

---

## Running it

```bash
npm install
npm run dev          # http://localhost:5173
```

The app is **fully functional with no API key.** Without a key it produces a real,
evidence-grounded diagnosis from the built-in rules engine, labelled `Rule-based`.

| Script | Purpose |
| --- | --- |
| `npm run dev` | Dev server, including the `/api` routes |
| `npm run build` | Typecheck + production build |
| `npm run preview` | Serve the production build, `/api` routes included |
| `npm test` | 42 tests over the analyzer, guards, and render paths |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run data` | Regenerate the mock datasets |

### Enabling the AI

```bash
cp .env.example .env
# then edit .env:
ANTHROPIC_API_KEY=sk-ant-...
```

Restart the dev server. The header changes from `AI offline` to `AI ready`.

`ANTHROPIC_API_KEY` is read **only** inside the Node side of Vite
([vite.config.ts](vite.config.ts) → [server/analyze.ts](server/analyze.ts)). It is never
prefixed with `VITE_`, never passed to `define`, and never reaches the browser. Verified
against the built bundle: zero occurrences of the key name, and the Anthropic SDK is not
in the client bundle at all.

Set `DISABLE_AI=1` to force the rules engine and spend nothing.

---

## Cost control

The model is used for **one thing only**: causal reasoning about a single transaction.

**Never sent to a model:** transaction search, status mapping, node states, money
booleans, cohort statistics, averages, percentages, timestamps, durations, filtering,
sorting, animations, or any UI rendering. All of that is deterministic code.

Protections in place:

1. **Explicit trigger only.** The model runs when you press *Run AI investigation* or ask
   a causal question. No call happens on page load, on transaction switch, on keystroke,
   or during render. There is no polling and no autonomous loop.
2. **Caching by `transactionId` + data hash.** A cache hit costs nothing. The hash
   deliberately **excludes wall-clock time** — the demo clock shifts on every page load,
   so hashing timestamps would miss the cache on every reload and re-bill for an identical
   question. Cohort counts are excluded for the same reason.
3. **Local-first question routing.** [`src/lib/commands.ts`](src/lib/commands.ts) answers
   lookup questions ("how long has it been pending?", "where is the money?", "show failed
   events", "can I retry?") from the trace, instantly and for free. Only *why*-shaped
   questions escalate.
4. **Minimal payload.** Only the records for the transaction under investigation are
   sent — never the corpus, never the CSVs. A payload is under 3 KB (asserted in tests).
5. **Tight output.** Structured tool output, `max_tokens: 1200`, `effort: low`.

Roughly **$0.017 per uncached investigation** on `claude-opus-5`; the six demo
transactions cost about **$0.10 total, once**, then never again.

---

## Architecture

```
src/
  data/            gateway.csv, bank.csv, ledger.csv, transactions.json  (generated)
  lib/
    dataset.ts     CSV parsing, indexing, demo-clock alignment
    trace.ts       correlation + node states + money state + incident signature
    similar.ts     cohort statistics
    health.ts      per-system health
    commands.ts    local-first question routing
    rulesDiagnosis.ts  deterministic diagnosis (fallback + $0 dev path)
    aiPayload.ts   minimal payload + cache hash
    aiClient.ts    cache -> fetch -> validate -> fallback
    cache.ts       memory + localStorage
    format.ts      INR currency, durations
  components/      TransactionTimeline, AIInsightCard, EvidenceDrawer, MoneyStatusCard,
                   RecommendedAction, SimilarTransactions, CommandBar, RawEvents,
                   SystemHealth, primitives
server/analyze.ts  server-side model call (key lives here)
scripts/           data generator, test runner
```

`trace.ts` is the single source of truth. The AI consumes its output and returns a
diagnosis; it never produces states, booleans, or statistics.

### Why the AI cannot invent evidence

Every evidence item has a stable id (`bank-no-response`, `ledger-no-events`). The model
must cite ids, and [`aiClient.ts`](src/lib/aiClient.ts) discards any id that is not on the
trace. A cause left citing nothing real is rejected outright and the rules diagnosis is
shown instead. Additionally:

- Failure codes are read from the logs, never taken from the model.
- Confidence is capped at 92% when the evidence is absence-based rather than a stated code.
- A `WEAK` trace suppresses any single cause even if the model asserts one.
- A retry is never advised once money has moved, regardless of what the model says.

Absences are first-class evidence — "ledger update never received" is a citable fact, so
the model can reference a gap without fabricating an event.

---

## Demo scenarios

| Transaction | Behaviour |
| --- | --- |
| `TXN_SUCCESS_001` | Settles end to end; all three nodes resolve green |
| `TXN_BANK_TIMEOUT_001` | Gateway accepted, bank never acknowledged; ledger never reached |
| `TXN_PROCESSOR_DELAY_001` | Bank acknowledged then reported `BNK_QUEUE_BACKLOG` |
| `TXN_LEDGER_DELAY_001` | Money **has** settled; ledger has not posted — do not re-initiate |
| `TXN_GATEWAY_FAILED_001` | Hard gateway rejection; downstream nodes render unavailable |
| `TXN_UNKNOWN_001` | Gateway log missing → refuses to name a cause, offers weighted alternatives |

Search for a nonexistent ID to see the *Transaction not found* state.

The dataset holds 91 transactions and 571 events. Cohort statistics are computed, not
hardcoded: the bank-timeout case reports 23 peers, 21 settled and 2 reversed, because that
is what the data contains.

---

## Animation

Five reusable node states — `SUCCESS`, `PROCESSING`, `PENDING`, `FAILED`, `UNKNOWN` — as
pure CSS keyframes, driven by a `data-state` attribute. A luminous pulse travels along each
connector and nodes arrive in sequence via an `--i` index delay. When progress halts the
pulse stops, downstream connectors go dashed, and downstream nodes render muted.

No animation library. Only `transform`/`opacity`/`box-shadow` are animated, so nothing
forces layout. `prefers-reduced-motion` holds every animation at its final frame, and
state stays distinguishable by glyph, colour, and border style independently.

---

## Accessibility

Semantic buttons throughout, visible focus rings on everything interactive, `aria-expanded`
on disclosures, `aria-pressed` on the monitor toggle, `role="meter"` on the confidence bar,
`aria-live` on results. Dialogs use `role="dialog"`/`aria-modal`, close on `Escape`,
contain `Tab`, take focus on open, and restore it on close. Status is never conveyed by
colour alone — every state carries a glyph and a text label.

---

## Verified

- `npm test` — 42/42 pass
- `npm run typecheck` — clean (`strict`, `noUncheckedIndexedAccess`)
- `npm run build` — clean; 70 KB gzipped JS
- All six scenarios plus the not-found state render (`react-dom/server` smoke tests)
- API routes on dev **and** preview: `503` with no key, `400` on bad JSON, `405` on `GET`
- No-key path degrades to the rules diagnosis with the timeline intact
- Built bundle contains no key reference and no Anthropic SDK

### Not verified

- **A live model call.** No API key was available in the build environment, so the
  successful `200` path through `server/analyze.ts` has never executed. Error, fallback,
  and validation paths are all tested; the happy path is not.
- **Browser rendering and animation smoothness.** Verified via SSR and the production
  build, not in a real browser. No visual or cross-browser check was performed.
