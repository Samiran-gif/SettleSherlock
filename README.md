# SettleSherlock — Investigation AI Agent

Root-cause analysis for suspicious financial transactions.

Given transaction evidence, the agent answers eight questions and returns a
**structured report** rather than free-form text:

| Question | Report field |
|---|---|
| What happened? | `case_summary`, `timeline`, `confirmed_facts` |
| Which transactions look suspicious? | `suspicious_transactions` |
| Which entities are involved? | `entities_involved` |
| What unusual patterns exist? | `detected_patterns` |
| What is the likely root cause? | `root_cause` |
| What evidence supports it? | `evidence` (each item cites a transaction id) |
| How confident are we? | `root_cause.confidence` + `data_quality` |
| What else should we ask for? | `recommended_next_steps`, `unknowns` |

## The core guarantee: calculated facts vs. AI interpretation

Everything that could be wrong in a costly way is computed in code:

* amounts, medians, MAD-based outlier scores, failure rates, inter-arrival gaps
* duplicate and near-duplicate matching, state-machine contradictions
* graph structure (chains, cycles, fan-in/fan-out)
* evidence-to-transaction links
* the confidence score, from a single documented formula

A language model is **optional and additive**. When enabled it receives a
digest of the findings — never the raw ledger — and may only produce prose.
Code, not prompt instructions, enforces this: only five whitelisted keys are
copied out of its response, so it cannot change a severity, a confidence, a
pattern or an evidence link. Everything it writes is tagged
`provenance: "llm"`, and `meta.deterministic_case_summary` always preserves the
computed summary.

Run with no configuration at all and you get a complete investigation with
`meta.llm_enrichment == "not_applied"`.

---

## Architecture

```
investigation-ai/
├── agent/
│   ├── models.py         Typed report model, enums, Provenance, JSON contract
│   ├── config.py         Every detection threshold, in one place
│   ├── validation.py     (1) Ingest, validate, normalize; data-quality report
│   ├── analyzer.py       (2) Amount/timing/status statistics, robust z-scores
│   ├── relationships.py  (3) Directed entity graph: cycles, paths, fan hubs
│   ├── patterns.py       (4) 16 deterministic detectors
│   ├── timeline.py       (5) Chronology with importance marking
│   ├── root_cause.py     (6, 9) Auditable rule base + recommendations
│   ├── evidence.py       (7, 8) Evidence linking + confidence scoring
│   ├── llm.py            Optional narrative layer (Anthropic), off by default
│   ├── investigator.py   Orchestrator
│   ├── service.py        Framework-agnostic seam for the backend API
│   └── cli.py            `python -m agent.cli case.json`
├── examples/             Six realistic evidence files
├── tests/                149 tests; no API key, no network
├── requirements.txt      (empty — the core is standard library only)
├── requirements-llm.txt  Optional LLM extras
├── requirements-dev.txt  pytest
└── .env.example
```

Pipeline order: **validate → analyze → relate → detect → timeline → reason →
link evidence → score → (optional) narrate.** Each stage is a pure function of
the previous one, so any stage can be tested or reused on its own.

### The 16 detectors

| Pattern id | Severity | What it means |
|---|---|---|
| `duplicate_settlement_reference` | critical | One reference/idempotency key settled twice or more |
| `double_charge_after_retry` | critical | Failed attempt(s) followed by two or more settlements |
| `near_duplicate_transaction` | high | Same party/amount/currency inside the duplicate window |
| `settlement_after_reversal` | high | Money moved again after the payment was reversed |
| `conflicting_records` | high | One `transaction_id`, two versions of the truth |
| `pass_through_chain` | high | A→B→C with value preserved at each hop |
| `circular_flow` | high | Funds return to the originating account |
| `fan_in` / `fan_out` | high | Many→one or one→many in a tight window |
| `threshold_structuring` | high | Amounts banded just under a reporting threshold |
| `amount_outlier` | high/med | Robust z-score or median-ratio outlier (upside only) |
| `transaction_burst` | high/med | One sender's densest 5-minute window, far above its own baseline |
| `orphan_reversal` | medium | A reversal with no matching original |
| `shared_reference_mixed_state` | medium | One reference, contradictory statuses |
| `processor_failure_cluster` | medium | Failures concentrated on one gateway or bank |
| `self_transfer` | medium | Sender and receiver are the same entity |
| `repeated_identical_amount` | med/low | Same amount repeatedly (low if on a fixed interval) |
| `multi_currency_account` | low | One account operating in several currencies |
| `off_hours_activity` | low | Activity concentrated overnight (UTC) |

Weak signals are reported at low severity rather than suppressed, and severity
never escalates on volume alone — a `high` case becomes `critical` only when
three or more independent serious patterns implicate at least half the records.

Deliberate design choices that avoid noise:

* Bursts are measured **per sending account**. Many different senders
  transacting at once is a fan-in, not a burst; treating it as one would fire
  on every busy merchant.
* Only **upside** amount outliers are reported — an unusually small transfer
  carries no exposure and would bury the real signal.
* Overlapping chains are collapsed to the **maximal** path, so one flow of
  funds is one finding rather than five.
* `repeated_identical_amount` on a fixed interval drops to `low` and actively
  *weakens* the double-submission hypothesis, because that shape is what
  subscriptions and payroll look like.

---

## Input format

A list of transaction records, a dict wrapping them under `transactions` /
`records` / `evidence` / `data` / `items`, or a single record.

```json
{
  "transactions": [
    {
      "transaction_id": "TXN-1001",
      "timestamp": "2026-08-14T10:00:00Z",
      "sender": "ACC-CUST-77",
      "receiver": "MERCH-ELEC-42",
      "amount": 2500.0,
      "currency": "USD",
      "transaction_type": "card_payment",
      "account_id": "ACC-CUST-77",
      "bank": "First Meridian",
      "gateway": "PayFlow",
      "status": "failed",
      "reference_id": "ORD-88231",
      "metadata": { "gateway_code": "TIMEOUT", "attempt": 1 }
    }
  ]
}
```

**No field is required.** The agent is built for incomplete real-world exports:

* Field aliases are accepted (`txn_id`, `from`, `to`, `value`, `ccy`, `state`,
  `psp`, `idempotency_key`, …).
* Amounts parse from `1250`, `"1,250.00"`, `"$1250"`, `"USD 1250"`.
* Timestamps parse from ISO-8601 (with or without `Z`), common `strptime`
  layouts, and epoch seconds/milliseconds; everything is normalized to UTC.
* ~40 raw status strings map onto seven canonical states
  (`success`, `failed`, `pending`, `reversed`, `refunded`, `cancelled`,
  `unknown`).
* A missing `transaction_id` gets a synthetic placeholder; a missing amount,
  timestamp or party becomes a `ValidationIssue` and an entry in `unknowns`.
* A record that is not an object at all is rejected and counted — the rest of
  the case still runs.

Nothing here raises on bad input. Garbage produces a low-confidence report
that explains why it is low-confidence.

## Output format

```json
{
  "case_summary": "...",
  "case_summary_source": "deterministic | llm",
  "severity": "low | medium | high | critical",
  "suspicious_transactions": [
    { "transaction_id": "TXN-1003", "risk_score": 0.95, "severity": "critical",
      "reasons": ["Duplicate settlement under one reference"],
      "pattern_ids": ["duplicate_settlement_reference"], "amount": 2500.0,
      "timestamp": "2026-08-14T10:00:41+00:00", "status": "success" }
  ],
  "entities_involved": [
    { "entity_id": "ACC-CUST-77", "entity_type": "account", "roles": ["sender"],
      "transaction_count": 3, "total_amount": 7500.0,
      "linked_entities": ["First Meridian", "MERCH-ELEC-42", "PayFlow"],
      "flags": ["implicated_by_pattern"] }
  ],
  "timeline": [
    { "timestamp": "...", "transaction_id": "TXN-1001", "event": "...",
      "importance": "high", "details": { "related_patterns": ["..."] } }
  ],
  "detected_patterns": [
    { "pattern_id": "...", "name": "...", "description": "...",
      "severity": "critical", "confidence": 0.8, "transaction_ids": ["..."],
      "entities": ["..."], "metrics": {},
      "grade": "confirmed_fact", "provenance": "deterministic" }
  ],
  "root_cause": {
    "conclusion": "...", "confidence": 0.76, "reasoning": ["..."],
    "supporting_pattern_ids": ["..."], "supporting_transaction_ids": ["..."],
    "contradicting_observations": [], "grade": "strong_evidence",
    "provenance": "deterministic"
  },
  "evidence": [
    { "transaction_id": "TXN-1002", "finding": "...", "importance": "high",
      "pattern_id": "...", "grade": "confirmed_fact",
      "provenance": "deterministic" }
  ],
  "alternative_hypotheses": [ { "conclusion": "...", "confidence": 0.55 } ],
  "unknowns": ["..."],
  "recommended_next_steps": ["..."],
  "confirmed_facts": ["..."],
  "statistics": { "amounts": {}, "timing": {}, "status_counts": {} },
  "data_quality": { "records_parsed": 6, "field_completeness": 1.0,
                    "conflicting_record_count": 0, "issues": [] },
  "ai_narrative": null,
  "meta": { "agent_version": "1.0.0", "llm_enrichment": "not_applied",
            "graph": {}, "config": {}, "duration_ms": 3.4 }
}
```

### Epistemic labelling

Every pattern and hypothesis carries a `grade`, which is how the report keeps
facts and guesses apart:

* `confirmed_fact` — directly verifiable in the submitted records
  ("this reference id appears on two successful settlements").
* `strong_evidence` — a measured statistical or structural finding.
* `hypothesis` — an explanation inferred from the above.
* `unknown` — a gap in the evidence.

### How confidence is computed

With supporting patterns:

```
confidence = 0.55 × strongest_pattern_confidence
           + 0.30 × data_quality
           + 0.15 × corroboration          (independent patterns, saturates at 4)
         then × the rule's prior, −0.10 per contradicting observation
```

With no supporting patterns the conclusion is "nothing suspicious found", whose
credibility depends only on the data:

```
confidence = 0.15 + 0.75 × data_quality
data_quality = 0.45 × field_completeness + 0.25 × volume + 0.30 × integrity
```

Results are clamped away from both 0.0 and 1.0 — the agent never claims
certainty, and it never treats sparse data as an all-clear. `root_cause.reasoning`
always ends with the arithmetic that produced the number.

---

## Running it

No installation is required for the core agent (Python 3.10+, standard library
only).

```powershell
cd investigation-ai

# Full JSON report
python -m agent.cli examples/duplicate_charge.json

# Human-readable digest
python -m agent.cli examples/duplicate_charge.json --summary

# From stdin, compact JSON, also written to a file
Get-Content examples/burst_drain.json | python -m agent.cli - --compact --out report.json
```

From Python:

```python
from agent import investigate

report = investigate(transactions)            # list of dicts
print(report.severity.value, report.root_cause.confidence)
payload = report.to_dict()                     # JSON-safe contract
```

Tuning thresholds:

```python
from agent import InvestigationConfig, investigate

report = investigate(
    transactions,
    config=InvestigationConfig(duplicate_window_seconds=900, burst_min_count=6),
)
```

### Wiring it into the backend

`agent/service.py` is the integration seam — no web framework is a dependency
of this package.

```python
from fastapi import FastAPI
from agent.service import health, run_investigation

app = FastAPI()

@app.post("/investigations")
def create_investigation(body: dict) -> dict:
    # body: {"transactions": [...], "options": {...}}  → JSON-safe report dict
    return run_investigation(body)

@app.get("/investigations/health")
def investigation_health() -> dict:
    return health()
```

`run_investigation` never raises on bad input: malformed evidence comes back as
a low-confidence report whose `data_quality` section explains the problem.
`Investigator` is stateless between calls, so one instance can be shared across
requests.

## Running the tests

```powershell
cd investigation-ai
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

The suite covers all eight required scenarios — normal activity, duplicates,
suspicious bursts, failed→successful sequences, multiple connected accounts,
missing fields, conflicting evidence and insufficient evidence — plus the
report contract, determinism, threshold configurability, and the LLM safety
boundary. It needs **no API key and makes no network calls**: the one LLM test
injects a fake provider.

## Configuring an LLM provider

Optional. Copy `.env.example` to `.env` (git-ignored) and set:

```ini
SETTLESHERLOCK_LLM_ENABLED=true
SETTLESHERLOCK_LLM_PROVIDER=anthropic
SETTLESHERLOCK_LLM_MODEL=claude-opus-5
ANTHROPIC_API_KEY=sk-ant-...
```

Then:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-llm.txt
python -m agent.cli examples/duplicate_charge.json --llm --summary
```

| Variable | Default | Purpose |
|---|---|---|
| `SETTLESHERLOCK_LLM_ENABLED` | `false` | Master switch; nothing is sent when unset |
| `SETTLESHERLOCK_LLM_PROVIDER` | `anthropic` | `anthropic` or `none` |
| `SETTLESHERLOCK_LLM_MODEL` | `claude-opus-5` | Narrative model |
| `SETTLESHERLOCK_LLM_FALLBACK_MODEL` | *(unset)* | Server-side retry model on a policy refusal |
| `SETTLESHERLOCK_LLM_MAX_TOKENS` | `8000` | Output cap |
| `SETTLESHERLOCK_LLM_TIMEOUT_SECONDS` | `60` | Request timeout |
| `ANTHROPIC_API_KEY` | — | Read by the Anthropic SDK, never by this code |

No credential is read, logged or written by this codebase: `AnthropicProvider`
passes `api_key=None` and lets the SDK resolve the credential itself. To add
another vendor, implement the two-member `LLMProvider` protocol in `agent/llm.py`
and return it from `provider_from_env`.

When enrichment is on, the report gains an `ai_narrative` object
(`case_summary`, `narrative`, `pattern_interpretation`,
`hypothesis_assessment`, `additional_evidence_requests`), `case_summary_source`
becomes `"llm"`, and AI-suggested next steps are prefixed
`[AI suggestion]`. Any failure — missing SDK, network error, bad JSON, policy
refusal — silently degrades to the deterministic report.

---

## Example investigation

Input: [`examples/duplicate_charge.json`](examples/duplicate_charge.json) — a
customer reports being charged twice. Six records: one payment that timed out
and was retried, plus four unrelated payments to the same merchant.

```powershell
python -m agent.cli examples/duplicate_charge.json --summary
```

Actual output (line-wrapped here for readability):

```
SEVERITY : CRITICAL
SUMMARY  : 6 transaction record(s) analysed, totalling 8176.49 USD, spanning
           2026-08-14 to 2026-08-14. 4 pattern(s) detected — most significant:
           Duplicate settlement under one reference; Double charge after retry;
           Near-duplicate transactions. Working conclusion: The same payment was
           settled more than once. ... Case severity: critical.
ROOT CAUSE (0.76 confidence):
  The same payment was settled more than once. The evidence is most consistent
  with a duplicate-charge defect in the payment submission or retry path rather
  than two genuine customer payments.
  - Observed [duplicate_settlement_reference] Reference 'ORD-88231' settled 2
    times (TXN-1002, TXN-1003), moving 5000.0 USD (detector confidence 0.80;
    evidence: TXN-1002, TXN-1003)
  - Observed [double_charge_after_retry] 1 failed attempt(s) of 2500.0 USD from
    ACC-CUST-77 to MERCH-ELEC-42 were followed by 2 successful settlements
    (detector confidence 0.72; evidence: TXN-1001, TXN-1002, TXN-1003)
  - Two settlements sharing one reference/idempotency key cannot both be
    authorised instructions; the reference is by definition unique per payment
    intent.
  - The duplicated leg should be refundable without customer dispute.
  - Confidence model: 0.55 * pattern_strength + 0.30 * data_quality + 0.15 *
    corroboration (pattern_strength=0.8, data_quality=0.9, corroboration=0.333)
    × rule prior 1.0 → 0.76

PATTERNS:
  [critical] Duplicate settlement under one reference (0.80) — Reference
             'ORD-88231' settled 2 times (TXN-1002, TXN-1003), moving 5000.0 USD
  [critical] Double charge after retry (0.72) — 1 failed attempt(s) of 2500.0
             USD from ACC-CUST-77 to MERCH-ELEC-42 were followed by 2 successful
             settlements
  [high    ] Near-duplicate transactions (0.78) — 2 transfers of 2500.0 USD from
             ACC-CUST-77 to MERCH-ELEC-42 within 29s
  [medium  ] Repeated identical amount (0.45) — 3 transfers of exactly 2500.0
             USD from ACC-CUST-77 to MERCH-ELEC-42

SUSPICIOUS TRANSACTIONS:
  TXN-1002   risk=0.95  Double charge after retry, Duplicate settlement under one
                        reference, Near-duplicate transactions, Repeated
                        identical amount
  TXN-1003   risk=0.95  (same four reasons)
  TXN-1001   risk=0.77  Double charge after retry, Repeated identical amount

ALTERNATIVE HYPOTHESES:
  (0.46) An identical payment instruction was submitted more than once in quick
         succession — consistent with a double submission (client double-click,
         webhook replay or batch re-run) rather than intentional misuse.

RECOMMENDED NEXT STEPS:
  - Retrieve the gateway request log for the shared reference id, including
    client retry counts and idempotency-key handling.
  - Confirm with the ledger whether both settlements were posted to the customer
    account or only one.
  - Check whether a refund or reversal has already been issued for the
    duplicated leg.
  - Pull the request ids / client fingerprints for the duplicate cluster to
    distinguish a UI double-submit from a server-side replay.
  - Confirm whether the receiving ledger recorded both legs.
```

Note what the agent does *not* do. It does not accuse the customer; it keeps
the benign "double submission" explanation as a ranked alternative instead of
collapsing two explanations into one; that alternative is *penalised* to 0.46
because `repeated_identical_amount` argues against it; and `TXN-1004`–`TXN-1006`
are left out of `suspicious_transactions` entirely. Confidence stops at 0.76,
not 0.95, because the case has six records rather than a full statistical
sample.

Try the others:

| Example | Demonstrates |
|---|---|
| `normal_activity.json` | Clean case → `low`, no findings, high confidence |
| `duplicate_charge.json` | Duplicate settlement + retry double-charge |
| `burst_drain.json` | Overnight burst, fan-out, account-compromise hypothesis |
| `layering_chain.json` | Pass-through chain and circular flow across four accounts |
| `incomplete_evidence.json` | Missing/malformed fields → explicit `unknowns` |
| `conflicting_records.json` | Self-contradicting evidence → reconciliation defect |
