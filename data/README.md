# SettleSherlock — Demo Data (`data/`)

This folder holds the **fake (synthetic) dataset** that SettleSherlock investigates.

SettleSherlock answers merchant questions like:

> "Why wasn't my settlement processed?"

To answer that, it has to follow the money through five steps:

```
Transaction  ->  Gateway  ->  Settlement  ->  Bank  ->  Ledger
```

Each step lives in one CSV file in this folder. Together they form a small,
connected story with **deliberate failures planted in it** so the AI agent has
something real to discover.

> ⚠️ **Everything here is invented.** No real people, no real card numbers, no
> real bank accounts, no real money. The merchant names, customer ids and
> amounts were all made up by a script.

---

## 1. Quick start

Open the VS Code terminal in the project root (`settleai`) and run:

```powershell
python data/generate_data.py
```

That creates (or re-creates) all 5 CSV files. Then check they are healthy:

```powershell
python data/validate_data.py
```

You should see `Overall result: PASS`.

Nothing needs to be installed — both scripts use only Python's built-in
libraries.

---

## 2. What each file is

| File | Think of it as | Rows |
|---|---|---|
| `transactions.csv` | Every individual payment a customer made | 155 |
| `settlements.csv` | Batches of money we try to pay out to merchants | 14 |
| `gateway_logs.csv` | The payment gateway's diary of events | 337 |
| `bank_logs.csv` | What the bank replied when we asked for a payout | 31 |
| `ledger.csv` | The accounting book (credits, debits, balance) | 25 |
| `generate_data.py` | The script that builds all of the above | — |
| `validate_data.py` | The script that checks all of the above | — |

### `transactions.csv`

One row = one customer payment.

| Column | Meaning |
|---|---|
| `transaction_id` | Unique id, e.g. `TXN-10001` |
| `merchant_id` | Which shop received the payment, e.g. `MER-001` |
| `customer_id` | Which (fake) customer paid, e.g. `CUS-029` |
| `amount` | How much, e.g. `842.21` |
| `currency` | `INR` or `USD` |
| `gateway` | `DemoPay`, `PayFlux` or `QuickSettle` |
| `transaction_status` | `SUCCESS`, `FAILED` or `PENDING` |
| `transaction_time` | When it happened, `2026-08-24 08:27:00` |
| `settlement_id` | Which payout batch it went into, e.g. `SET-001` |

**Important rule:** only `SUCCESS` transactions get a `settlement_id`.
A `FAILED` or `PENDING` payment never reaches the bank, so its
`settlement_id` is **empty** — exactly like a real system. Transactions dated
`2026-09-04` (our "today") are also empty because they haven't been batched yet.

### `settlements.csv`

One row = one payout batch for one merchant for one day.

| Column | Meaning |
|---|---|
| `settlement_id` | Unique id, e.g. `SET-005` |
| `merchant_id` | Who is being paid |
| `settlement_date` | When the batch was created (always 22:05 the same day) |
| `total_amount` | Sum of that batch's `SUCCESS` transactions |
| `currency` | `INR` or `USD` |
| `settlement_status` | `SUCCESS`, `PROCESSING`, `FAILED`, `ON_HOLD`, `PARTIAL` |
| `expected_payout_date` | When the merchant *should* have been paid (next day, "T+1") |
| `actual_payout_date` | When they really were paid — **empty if never paid** |
| `bank_reference` | The bank's id for the payout, e.g. `BANK-005` — **empty if never sent to the bank** |
| `failure_reason` | Short code, e.g. `BANK_REJECTED` — empty when nothing is wrong |

`failure_reason` values used: `BANK_REJECTED`, `AWAITING_BANK_CONFIRMATION`,
`PARTIAL_TRANSFER`, `COMPLIANCE_REVIEW`, `GATEWAY_ERROR`,
`INVALID_BANK_ACCOUNT`, `BANK_TIMEOUT`.

### `gateway_logs.csv`

The gateway writes a line every time something happens. Two kinds of rows:

* **transaction events** — `transaction_id` is filled in
  (`PAYMENT_RECEIVED`, `PAYMENT_AUTHORIZED`, `PAYMENT_FAILED`)
* **settlement events** — `settlement_id` is filled in, `transaction_id` is empty
  (`SETTLEMENT_CREATED`, `SETTLEMENT_SUBMITTED`)

| Column | Meaning |
|---|---|
| `log_id` | Unique id, e.g. `GWL-00001` |
| `transaction_id` | Which payment (empty for settlement events) |
| `settlement_id` | Which batch (empty for failed/pending payments) |
| `gateway` | Which gateway wrote the line |
| `event_type` | What happened (see list above) |
| `status` | `RECEIVED`, `AUTHORIZED`, `FAILED`, `PENDING`, `CREATED`, `SUBMITTED`, `ON_HOLD` |
| `timestamp` | When |
| `response_code` | `200`, `201`, `202`, `400`, `402`, `500`, `504` |
| `message` | Human-readable explanation |

### `bank_logs.csv`

The bank's side of the conversation. One settlement usually has 1–3 rows.

| Column | Meaning |
|---|---|
| `bank_log_id` | Unique id, e.g. `BNK-0014` |
| `settlement_id` | Which batch this reply is about |
| `bank_reference` | Matches `settlements.bank_reference` |
| `timestamp` | When the bank replied |
| `status` | `ACCEPTED`, `PROCESSING`, `COMPLETED`, `REJECTED`, `TIMEOUT` |
| `response_code` | `200`, `202`, `206`, `400`, `402`, `504` |
| `message` | Why, in plain English |

**If a settlement has no rows here at all, the bank never even saw it.**
That is a real clue (see SC-05 and SC-06 below).

### `ledger.csv`

The accounting book. Money is tracked as credits and debits.

| Column | Meaning |
|---|---|
| `ledger_id` | Unique id, e.g. `LED-0009` |
| `settlement_id` | Which batch this entry belongs to |
| `merchant_id` | Whose account it affects |
| `entry_type` | `SETTLEMENT_CREDIT`, `SETTLEMENT_DEBIT`, `REVERSAL`, `ADJUSTMENT` |
| `amount` | The size of the entry |
| `debit` | Money going out (one of debit/credit is always `0.00`) |
| `credit` | Money coming in |
| `balance` | Running balance for that merchant after this entry |
| `status` | `POSTED`, `PENDING`, `ON_HOLD`, `PARTIAL`, `FAILED`, `REVERSED` |
| `timestamp` | When it was posted |
| `description` | Plain-English note |

What the entry types mean:

* `SETTLEMENT_CREDIT` — "we now **owe** the merchant this money"
* `SETTLEMENT_DEBIT` — "we **sent** the money out to the bank"
* `REVERSAL` — "the payout failed, so we put the money **back**"
* `ADJUSTMENT` — a fee or manual correction

**Reading the balance:** `balance = previous balance + credit − debit`, counted
per merchant in time order. A healthy settlement is a credit followed by a
matching debit, so the balance comes back to where it started. If a balance
stays high, money is stuck. If it goes **negative**, we paid out more than we
recorded — that's a bug (and it is exactly what SC-08 does to `MER-004`).

---

## 3. How the files connect

```
                       transactions.csv
                       transaction_id ─────────┐
                       settlement_id ──┐       │
                                       │       │
                        settlements.csv│       │  gateway_logs.csv
                        settlement_id ◄┘       └► transaction_id
                             ▲  ▲   ▲             settlement_id ──┐
                             │  │   └─────────────────────────────┘
             bank_logs.csv ──┘  └── ledger.csv
             settlement_id          settlement_id
```

In words — the id you join on is almost always **`settlement_id`**:

* `transactions.settlement_id` → `settlements.settlement_id`
* `gateway_logs.transaction_id` → `transactions.transaction_id`
* `gateway_logs.settlement_id` → `settlements.settlement_id`
* `bank_logs.settlement_id` → `settlements.settlement_id`
* `ledger.settlement_id` → `settlements.settlement_id`
* `settlements.bank_reference` → `bank_logs.bank_reference`

To find the **failed** transactions related to a settlement, you cannot use
`settlement_id` (failed payments don't have one). Instead filter
`transactions.csv` by the settlement's `merchant_id` **and** the date part of
its `settlement_date`. All of a settlement's transactions happen on that same
calendar day, between 08:00 and 20:00.

Also true of this dataset (the validator enforces it):

* `settlements.total_amount` is **exactly** the sum of that batch's `SUCCESS`
  transaction amounts.
* Every `settlement_id` referenced anywhere really exists in `settlements.csv`.
* No duplicate ids anywhere.

---

## 4. The 12 investigation scenarios

These are the puzzles planted on purpose. The AI team should be able to solve
each one from the data alone.

| # | Scenario | Settlement(s) | Where the evidence is |
|---|---|---|---|
| **SC-01** | **Healthy settlement** — everything worked | `SET-001`, `SET-002`, `SET-010` | bank `COMPLETED`, ledger credit + matching debit, `actual_payout_date` filled |
| **SC-02** | **Bank rejected the payout** — no funds in the pool account | `SET-005` | `bank_logs` `REJECTED` / `402`; `failure_reason=BANK_REJECTED` |
| **SC-03** | **Bank timeout** — bank never answered | `SET-012` | `bank_logs` `TIMEOUT` / `504`; still `PROCESSING` |
| **SC-04** | **Stuck in processing** — accepted days ago, never confirmed | `SET-006` | `bank_logs` stops at `PROCESSING`; `expected_payout_date` is in the past; no `actual_payout_date` |
| **SC-05** | **On hold for compliance** — never sent to the bank | `SET-008` | **no rows in `bank_logs`**; `bank_reference` empty; gateway log `SETTLEMENT_CREATED` with status `ON_HOLD`; `failure_reason=COMPLIANCE_REVIEW` |
| **SC-06** | **Gateway failure blocked the settlement** | `SET-009` | gateway `SETTLEMENT_SUBMITTED` with status `FAILED` / `500`; **no rows in `bank_logs`**; `failure_reason=GATEWAY_ERROR` |
| **SC-07** | **Paid, but the ledger entry is missing** | `SET-003` | bank says `COMPLETED`, but **zero rows in `ledger.csv`** for `SET-003` |
| **SC-08** | **Ledger amount doesn't match the settlement** | `SET-004` | ledger `SETTLEMENT_CREDIT` is `1500.00` lower than `total_amount`; `MER-004`'s balance drifts to `-1500.00` |
| **SC-09** | **Settlement reversed** — bad bank account | `SET-011` (also `SET-005`) | `bank_logs` `REJECTED` / `400` "account number failed validation"; ledger has a `REVERSAL` entry |
| **SC-10** | **Many transactions failed before settlement** | `SET-013` | `MER-001` on `2026-09-02`: 8 of 13 transactions are `FAILED`, so the settlement total is unusually small |
| **SC-11** | **Partial settlement** — merchant underpaid | `SET-007` | `settlement_status=PARTIAL`; bank `COMPLETED` with code `206`; ledger debit is only ~60% of the credit |
| **SC-12** | **Fee adjustment** — payout fine, extra fee charged | `SET-014` | ledger has an `ADJUSTMENT` debit of `250.00` after the payout |

### Example investigation: "Why wasn't SET-008 paid?"

1. Look up `SET-008` in `settlements.csv`
   → status `ON_HOLD`, `failure_reason = COMPLIANCE_REVIEW`,
   `bank_reference` is empty, `actual_payout_date` is empty.
2. Look for `SET-008` in `bank_logs.csv` → **nothing**. The bank never got it.
3. Look for `SET-008` in `gateway_logs.csv` → there is a `SETTLEMENT_CREATED`
   row with status `ON_HOLD` and the message "held for compliance review;
   not submitted to bank", and **no** `SETTLEMENT_SUBMITTED` row.
4. Look in `ledger.csv` → the credit exists with status `ON_HOLD`, no debit.

**Answer:** the money was correctly calculated but compliance froze the batch
before it was submitted to the bank, so no payout was ever attempted.

### Example investigation: "SET-003 says SUCCESS — why don't my books balance?"

1. `settlements.csv` → `SET-003` is `SUCCESS`, paid on `2026-08-27`.
2. `bank_logs.csv` → `ACCEPTED` → `PROCESSING` → `COMPLETED`. The bank really
   paid it.
3. `ledger.csv` → **no rows at all for `SET-003`.**

**Answer:** the payout happened but the accounting entry was never written.
This is a book-keeping bug, not a payment problem.

---

## 5. How the generator works

`generate_data.py` is not random guesswork. Near the top there is a list called
`SETTLEMENT_PLAN` — one entry per settlement — that spells out the story of
each batch: how many transactions succeeded or failed, what the bank replied,
and what the ledger should look like. Everything else is derived from that plan.

If you want to change a scenario or add a new one, edit `SETTLEMENT_PLAN` and
re-run the script. Two notes:

* `RANDOM_SEED = 42` keeps the output identical on every machine and every run.
  Don't change it unless the whole team agrees, or your teammates' saved
  queries and test answers will drift.
* Re-running the generator **overwrites** the CSV files. That is normal and
  safe — the files are outputs, not hand-edited data.

## 6. How the validator works

`validate_data.py` checks the things that would silently break the backend:

1. required columns exist
2. transaction → settlement links are valid
3. gateway log → transaction / settlement links are valid
4. bank log → settlement links are valid
5. ledger → settlement links are valid (and the merchant matches)
6. amount and response-code fields are real numbers
7. required id fields are never empty
8. no duplicate primary ids
9. dates and timestamps use the right format
10. each settlement total equals the sum of its `SUCCESS` transactions

It then prints the row counts and a list of the **intentional** puzzles it can
detect. Those puzzles are reported for information only — they never cause a
`FAIL`. If you see `FAIL`, the report explains which file and which line is at
fault; re-running `python data/generate_data.py` fixes anything you may have
edited by accident.
