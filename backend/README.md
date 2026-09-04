# SettleSherlock — Backend

AI-powered settlement investigation system (PS-8). Python + FastAPI.

**Stage 4:** AI explanation layer over the deterministic engine.
`CSV files → data loader → lookup API → investigation engine → AI explanation`

## Layout

```
backend/
├── app/
│   ├── main.py                     # app factory, middleware, root endpoint
│   ├── core/config.py              # env-driven settings
│   ├── data/                       # mock CSV data (see below)
│   │   ├── gateway.csv
│   │   ├── bank.csv
│   │   └── ledger.csv
│   ├── services/
│   │   ├── data_loader.py           # reads + searches the CSVs
│   │   ├── investigation.py         # deterministic settlement rules
│   │   └── ai_explanation.py        # plain-English layer over the result
│   ├── api/
│   │   ├── router.py               # aggregates versioned routers
│   │   └── routes/
│   │       ├── health.py           # /api/v1/health
│   │       └── transactions.py     # lookup + investigation routes
│   └── schemas/
│       ├── common.py               # health / root / error models
│       ├── transaction.py          # transaction models
│       ├── investigation.py        # investigation models
│       └── explanation.py          # AI explanation models
├── tests/
├── requirements.txt
├── requirements-dev.txt
└── .env.example
```

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements-dev.txt   # or requirements.txt for runtime only
cp .env.example .env
```

## Run

```bash
uvicorn app.main:app --reload
```

| URL | Purpose |
| --- | --- |
| http://localhost:8000/ | Service metadata |
| http://localhost:8000/api/v1/health | Health check |
| http://localhost:8000/api/v1/transactions/TXN10001 | Transaction lookup |
| http://localhost:8000/api/v1/transactions/TXN10001/investigation | Investigation |
| http://localhost:8000/api/v1/transactions/TXN10001/explanation | AI explanation |
| http://localhost:8000/docs | Swagger UI |

## Mock data

> **These CSVs are mock hackathon data, not real or live financial records.**
> They exist so the API and the upcoming investigation engine have something
> to work against.

The three source systems live in [`app/data/`](app/data/), one CSV each:

| File | Columns |
| --- | --- |
| `gateway.csv` | `transaction_id,amount,status,timestamp,gateway_reference` |
| `bank.csv` | `transaction_id,amount,status,timestamp,bank_reference` |
| `ledger.csv` | `transaction_id,amount,status,timestamp,ledger_reference` |

Paths are resolved relative to the `app` package, so the server works from any
working directory. Demo scenarios:

| Transaction | Scenario |
| --- | --- |
| `TXN10001` | Normal successful settlement |
| `TXN10002` | Bank-side delayed settlement |
| `TXN10003` | Missing ledger record |
| `TXN10004` | Amount mismatch (gateway 7500 vs bank 7000) |
| `TXN10005` | Gateway failed |
| `TXN10006` | Missing bank record |
| `TXN10007` | Multiple inconsistencies (amount mismatch + delay + missing ledger) |

## API

### `GET /api/v1/transactions/{transaction_id}`

Searches all three CSVs for the same `transaction_id` and returns each
system's record. A system with no record comes back as `null`; a transaction
absent from **all three** returns `404`.

```bash
curl http://localhost:8000/api/v1/transactions/TXN10002
```

```json
{
  "transaction_id": "TXN10002",
  "gateway": {
    "transaction_id": "TXN10002",
    "amount": 5000.0,
    "status": "SUCCESS",
    "timestamp": "2026-08-30T10:32:00",
    "reference": "GW10002"
  },
  "bank": {
    "transaction_id": "TXN10002",
    "amount": 5000.0,
    "status": "DELAYED",
    "timestamp": "2026-08-30T16:45:00",
    "reference": "BANK10002"
  },
  "ledger": {
    "transaction_id": "TXN10002",
    "amount": 5000.0,
    "status": "RECORDED",
    "timestamp": "2026-08-30T10:35:00",
    "reference": "LEDGER10002"
  }
}
```

A partially present transaction is **not** an error — `TXN10003` returns its
gateway and bank records with `"ledger": null`. The investigation stage relies
on seeing those gaps.

Unknown transaction:

```bash
curl -i http://localhost:8000/api/v1/transactions/TXN99999
# HTTP/1.1 404 Not Found
# {"detail": "Transaction TXN99999 not found"}
```

### `GET /api/v1/transactions/{transaction_id}/investigation`

Runs the deterministic investigation engine
([`app/services/investigation.py`](app/services/investigation.py)) over the
same records. Every conclusion is derived from the CSVs — no transaction id is
special-cased and no AI is involved, so a given set of records always produces
the same verdict.

```bash
curl http://localhost:8000/api/v1/transactions/TXN10002/investigation
```

```json
{
  "transaction_id": "TXN10002",
  "status": "DELAYED",
  "root_cause": "Bank-side settlement delay",
  "investigation_confidence": 94,
  "evidence": [
    "Gateway status is SUCCESS",
    "Bank status is DELAYED",
    "Ledger status is RECORDED",
    "Gateway, Bank and Ledger amounts match at 5000.00",
    "Bank timestamp 2026-08-30T16:45:00 is 6h 13m later than the gateway timestamp 2026-08-30T10:32:00"
  ],
  "exceptions": [],
  "recommended_action": "Verify bank settlement batch and bank reference."
}
```

Statuses: `SETTLED`, `FAILED`, `DELAYED`, `INCOMPLETE`, `NEEDS_INVESTIGATION`.
When several problems coexist the status is `NEEDS_INVESTIGATION` and *all* of
them are listed, rather than one being singled out.

`investigation_confidence` (0-100) measures **how complete and consistent the
evidence is — not financial risk**. It starts at 100 and subtracts a fixed
penalty per problem found (missing record −30 each, amount mismatch or
conflicting statuses −15, unrecognised status −12, bank delay −6). A cleanly
failed transaction therefore still scores 100: all three systems agree on what
happened.

If the records cannot support a conclusion, `root_cause` says
`"Root cause cannot be confirmed from available evidence."` rather than
guessing.

### `GET /api/v1/transactions/{transaction_id}/explanation`

Runs the investigation, then asks an LLM to re-word the result for a support
agent. **The deterministic engine remains the source of truth** — its output is
returned unchanged under `investigation`, and the model is given nothing but
that result.

```bash
curl http://localhost:8000/api/v1/transactions/TXN10002/explanation
```

```json
{
  "transaction_id": "TXN10002",
  "explanation": "This transaction is delayed on the bank side. ...",
  "source": "ai",
  "ai_available": true,
  "model": "llama-3.3-70b-versatile",
  "notice": null,
  "investigation": { "...": "the full deterministic result" }
}
```

**Configuration.** Set `AI_API_KEY` in `.env` (gitignored; never commit it).
Any OpenAI-compatible chat-completions provider works — the defaults target
Groq's free tier, and switching to Gemini is an `AI_BASE_URL` / `AI_MODEL`
change with no code change. See [`.env.example`](.env.example).

**Without a key the endpoint still works.** It returns `source: "fallback"`,
`ai_available: false`, and an explanation that opens with "AI explanation is
unavailable" followed by the deterministic result. The same fallback covers
timeouts, HTTP errors and unparseable responses, so a dead provider never
becomes a 500.

**Grounding.** The prompt carries only the investigation result, so CSV
reference numbers never reach the provider. Any model output containing a
number absent from those facts is rejected and replaced with the fallback —
invented amounts, timestamps and references cannot reach the caller.

## Tests

```bash
pytest
```

## Adding an endpoint

1. Add a module under `app/api/routes/` exposing an `APIRouter` named `router`.
2. Register it in `app/api/router.py`.
3. Put request/response models in `app/schemas/`.
4. Keep data access in `app/services/`, not in the route.
