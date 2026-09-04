"""
SettleSherlock - synthetic (fake) demo data generator
=====================================================

WHAT THIS SCRIPT DOES
---------------------
It creates 5 CSV files inside this same `data/` folder:

    transactions.csv   -> individual payments made by customers
    settlements.csv    -> batches of money paid out to merchants
    gateway_logs.csv   -> what the payment gateway did, step by step
    bank_logs.csv      -> what the bank replied when we asked for a payout
    ledger.csv         -> the accounting book (credits / debits / balance)

HOW TO RUN IT
-------------
    python data/generate_data.py

IMPORTANT
---------
* All data here is FAKE / synthetic. No real people, cards or bank accounts.
* We use a fixed "random seed" (RANDOM_SEED below). That means every time you
  run this script you get the EXACT same numbers. This is important so the
  backend team and the AI team always test against the same data.
* Only Python's standard library is used, so nothing needs to be installed.

HOW TO READ THIS FILE (for beginners)
-------------------------------------
1. CONFIG section      -> the "settings" (merchants, dates, seed)
2. SCENARIO PLAN       -> the story of each settlement (this is the heart of it)
3. BUILD section       -> loops that turn the plan into rows
4. WRITE section       -> saves the rows into CSV files
"""

import csv
import os
import random
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 1. CONFIG
# ---------------------------------------------------------------------------

# A "seed" makes the random numbers repeatable. Same seed = same data.
RANDOM_SEED = 42

# Save the CSVs next to this script (so it works no matter where you run it).
DATA_DIR = os.path.dirname(os.path.abspath(__file__))

# The "today" of our demo world. All data happens before this date.
DEMO_TODAY = datetime(2026, 9, 4)

# Our fake merchants (shops that receive money).
# Each merchant always uses the same gateway, currency and typical basket size.
MERCHANTS = {
    "MER-001": {"name": "Chai Point Cafe",    "gateway": "DemoPay",     "currency": "INR", "min": 150.0,  "max": 1800.0},
    "MER-002": {"name": "Bhopal Books",       "gateway": "DemoPay",     "currency": "INR", "min": 300.0,  "max": 4500.0},
    "MER-003": {"name": "Nova Electronics",   "gateway": "PayFlux",     "currency": "INR", "min": 1200.0, "max": 26000.0},
    "MER-004": {"name": "Urban Threads",      "gateway": "PayFlux",     "currency": "INR", "min": 500.0,  "max": 7500.0},
    "MER-005": {"name": "Sunrise Pharmacy",   "gateway": "QuickSettle", "currency": "INR", "min": 90.0,   "max": 3200.0},
    "MER-006": {"name": "GlobalTech Exports", "gateway": "QuickSettle", "currency": "USD", "min": 40.0,   "max": 950.0},
}

# Fake customer ids: CUS-001 ... CUS-060
CUSTOMER_IDS = [f"CUS-{i:03d}" for i in range(1, 61)]

# ---------------------------------------------------------------------------
# 2. SCENARIO PLAN  (the most important part of this file)
# ---------------------------------------------------------------------------
# Every settlement below is a deliberate "test case" for the AI investigator.
# Nothing here is random: we decide in advance what went wrong and where the
# evidence lives (gateway logs / bank logs / ledger).
#
# Field meanings:
#   settlement_id  - the batch id, e.g. SET-005
#   merchant_id    - who gets the money
#   date           - the day the transactions happened AND the batch was created
#   n_success      - how many transactions succeeded (these make up the total)
#   n_failed       - how many transactions failed that day
#   n_pending      - how many transactions were still pending that day
#   status         - SUCCESS / PROCESSING / FAILED / ON_HOLD / PARTIAL
#   failure_reason - short machine-readable reason ("" when nothing is wrong)
#   gateway        - "normal" | "not_submitted" | "submit_failed"
#   bank           - which bank story to write (see build_bank_logs)
#   ledger         - which accounting story to write (see build_ledger)
#   fail_profile   - what kind of transaction failures happened that day
#   scenario       - our scenario code, documented in README.md
#   note           - plain-English explanation (also printed at the end)

SETTLEMENT_PLAN = [
    {
        "settlement_id": "SET-001", "merchant_id": "MER-001", "date": datetime(2026, 8, 24),
        "n_success": 12, "n_failed": 1, "n_pending": 0,
        "status": "SUCCESS", "failure_reason": "",
        "gateway": "normal", "bank": "completed", "ledger": "clean",
        "fail_profile": "declines", "scenario": "SC-01",
        "note": "Healthy settlement: money reached the bank and the ledger matches.",
    },
    {
        "settlement_id": "SET-002", "merchant_id": "MER-002", "date": datetime(2026, 8, 25),
        "n_success": 10, "n_failed": 1, "n_pending": 0,
        "status": "SUCCESS", "failure_reason": "",
        "gateway": "normal", "bank": "completed", "ledger": "clean",
        "fail_profile": "declines", "scenario": "SC-01",
        "note": "Healthy settlement (second clean example).",
    },
    {
        "settlement_id": "SET-003", "merchant_id": "MER-003", "date": datetime(2026, 8, 26),
        "n_success": 9, "n_failed": 0, "n_pending": 0,
        "status": "SUCCESS", "failure_reason": "",
        "gateway": "normal", "bank": "completed", "ledger": "missing",
        "fail_profile": "declines", "scenario": "SC-07",
        "note": "Bank paid the merchant but NO ledger entry was written (book-keeping bug).",
    },
    {
        "settlement_id": "SET-004", "merchant_id": "MER-004", "date": datetime(2026, 8, 27),
        "n_success": 8, "n_failed": 1, "n_pending": 0,
        "status": "SUCCESS", "failure_reason": "",
        "gateway": "normal", "bank": "completed", "ledger": "mismatch",
        "fail_profile": "declines", "scenario": "SC-08",
        "note": "Settlement succeeded but the ledger credit is 1500.00 lower than the settlement total.",
    },
    {
        "settlement_id": "SET-005", "merchant_id": "MER-005", "date": datetime(2026, 8, 28),
        "n_success": 11, "n_failed": 1, "n_pending": 0,
        "status": "FAILED", "failure_reason": "BANK_REJECTED",
        "gateway": "normal", "bank": "rejected_funds", "ledger": "reversal",
        "fail_profile": "declines", "scenario": "SC-02",
        "note": "Bank REJECTED the payout (402). Ledger shows a REVERSAL, so money is still owed.",
    },
    {
        "settlement_id": "SET-006", "merchant_id": "MER-001", "date": datetime(2026, 8, 29),
        "n_success": 9, "n_failed": 0, "n_pending": 1,
        "status": "PROCESSING", "failure_reason": "AWAITING_BANK_CONFIRMATION",
        "gateway": "normal", "bank": "stuck", "ledger": "pending",
        "fail_profile": "declines", "scenario": "SC-04",
        "note": "Stuck: bank accepted it days ago but never sent COMPLETED. Payout date is overdue.",
    },
    {
        "settlement_id": "SET-007", "merchant_id": "MER-002", "date": datetime(2026, 8, 30),
        "n_success": 10, "n_failed": 2, "n_pending": 0,
        "status": "PARTIAL", "failure_reason": "PARTIAL_TRANSFER",
        "gateway": "normal", "bank": "partial", "ledger": "partial",
        "fail_profile": "mixed", "scenario": "SC-11",
        "note": "Bank only moved part of the money (206). Merchant was paid less than the total.",
    },
    {
        "settlement_id": "SET-008", "merchant_id": "MER-006", "date": datetime(2026, 8, 30),
        "n_success": 7, "n_failed": 0, "n_pending": 0,
        "status": "ON_HOLD", "failure_reason": "COMPLIANCE_REVIEW",
        "gateway": "not_submitted", "bank": "none", "ledger": "on_hold",
        "fail_profile": "declines", "scenario": "SC-05",
        "note": "Held by compliance. Never submitted to the bank, so bank_logs has NO rows for it.",
    },
    {
        "settlement_id": "SET-009", "merchant_id": "MER-003", "date": datetime(2026, 8, 31),
        "n_success": 8, "n_failed": 3, "n_pending": 0,
        "status": "FAILED", "failure_reason": "GATEWAY_ERROR",
        "gateway": "submit_failed", "bank": "none", "ledger": "failed_no_payout",
        "fail_profile": "gateway_outage", "scenario": "SC-06",
        "note": "The gateway crashed (500) while submitting, so the bank never saw this settlement.",
    },
    {
        "settlement_id": "SET-010", "merchant_id": "MER-004", "date": datetime(2026, 9, 1),
        "n_success": 12, "n_failed": 0, "n_pending": 0,
        "status": "SUCCESS", "failure_reason": "",
        "gateway": "normal", "bank": "completed", "ledger": "clean",
        "fail_profile": "declines", "scenario": "SC-01",
        "note": "Healthy settlement (third clean example).",
    },
    {
        "settlement_id": "SET-011", "merchant_id": "MER-005", "date": datetime(2026, 9, 1),
        "n_success": 9, "n_failed": 1, "n_pending": 0,
        "status": "FAILED", "failure_reason": "INVALID_BANK_ACCOUNT",
        "gateway": "normal", "bank": "rejected_account", "ledger": "reversal",
        "fail_profile": "declines", "scenario": "SC-09",
        "note": "Bank rejected because the account number is invalid (400); a REVERSAL was posted.",
    },
    {
        "settlement_id": "SET-012", "merchant_id": "MER-006", "date": datetime(2026, 9, 2),
        "n_success": 8, "n_failed": 0, "n_pending": 0,
        "status": "PROCESSING", "failure_reason": "BANK_TIMEOUT",
        "gateway": "normal", "bank": "timeout", "ledger": "pending",
        "fail_profile": "declines", "scenario": "SC-03",
        "note": "Bank never answered in time (504 timeout). Still stuck in PROCESSING.",
    },
    {
        "settlement_id": "SET-013", "merchant_id": "MER-001", "date": datetime(2026, 9, 2),
        "n_success": 4, "n_failed": 8, "n_pending": 1,
        "status": "SUCCESS", "failure_reason": "",
        "gateway": "normal", "bank": "completed", "ledger": "clean",
        "fail_profile": "mixed", "scenario": "SC-10",
        "note": "Settlement is fine but tiny: 8 of that day's 13 transactions FAILED at the gateway.",
    },
    {
        "settlement_id": "SET-014", "merchant_id": "MER-002", "date": datetime(2026, 9, 3),
        "n_success": 10, "n_failed": 0, "n_pending": 2,
        "status": "SUCCESS", "failure_reason": "",
        "gateway": "normal", "bank": "completed", "ledger": "adjustment",
        "fail_profile": "declines", "scenario": "SC-12",
        "note": "Successful settlement with a 250.00 ADJUSTMENT (fee) deducted in the ledger.",
    },
]

# Transactions that happened "today" and have not been batched into a
# settlement yet. Their settlement_id is empty on purpose.
UNSETTLED_PLAN = [
    {"merchant_id": "MER-001", "status": "SUCCESS"},
    {"merchant_id": "MER-002", "status": "SUCCESS"},
    {"merchant_id": "MER-003", "status": "PENDING"},
    {"merchant_id": "MER-004", "status": "FAILED"},
    {"merchant_id": "MER-005", "status": "SUCCESS"},
    {"merchant_id": "MER-006", "status": "PENDING"},
]

# Reasons a single customer payment can fail at the gateway.
# (response_code, message)
DECLINE_FAILURES = [
    (402, "Card declined by issuing bank"),
    (402, "Insufficient balance on customer card"),
    (400, "Invalid CVV in payment request"),
]
GATEWAY_OUTAGE_FAILURES = [
    (500, "Gateway internal error while authorizing payment"),
    (504, "Gateway upstream timeout - no response from acquirer"),
    (504, "Gateway upstream timeout - no response from acquirer"),
]

# ---------------------------------------------------------------------------
# 3. SMALL HELPERS
# ---------------------------------------------------------------------------


def money(value):
    """Turn 2500 into the text '2500.00' (money always shows 2 decimals)."""
    return f"{round(value + 0.0, 2):.2f}"


def ts(dt):
    """Format a date+time like '2026-09-01 10:15:00'."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def day(dt):
    """Format a date only, like '2026-09-01'."""
    return dt.strftime("%Y-%m-%d")


def random_times(date, count):
    """Pick `count` different clock times between 08:00 and 20:00 on `date`."""
    minutes = sorted(random.sample(range(8 * 60, 20 * 60), count))
    return [date + timedelta(minutes=m) for m in minutes]


def random_amount(merchant_id):
    """A believable purchase amount for this merchant."""
    cfg = MERCHANTS[merchant_id]
    return round(random.uniform(cfg["min"], cfg["max"]), 2)


# ---------------------------------------------------------------------------
# 4. BUILD: TRANSACTIONS
# ---------------------------------------------------------------------------


def build_transactions():
    """
    Create every transaction row.

    Returns:
        transactions : list of dict rows (ready for the CSV)
        totals       : {settlement_id: total amount of its SUCCESS transactions}
    """
    transactions = []
    totals = {}
    next_txn = 10001  # transaction ids run TXN-10001, TXN-10002, ...

    for plan in SETTLEMENT_PLAN:
        merchant_id = plan["merchant_id"]
        cfg = MERCHANTS[merchant_id]

        # Build a list of statuses for that day, then shuffle so the successes
        # and failures are mixed through the day instead of grouped together.
        statuses = (["SUCCESS"] * plan["n_success"]
                    + ["FAILED"] * plan["n_failed"]
                    + ["PENDING"] * plan["n_pending"])
        random.shuffle(statuses)

        times = random_times(plan["date"], len(statuses))
        success_total = 0.0

        for status, when in zip(statuses, times):
            amount = random_amount(merchant_id)

            # Only SUCCESS transactions belong to a settlement batch.
            # Failed/pending payments never reach the bank, so we leave the
            # settlement_id empty (that is how a real system looks).
            settlement_id = plan["settlement_id"] if status == "SUCCESS" else ""
            if status == "SUCCESS":
                success_total += amount

            transactions.append({
                "transaction_id": f"TXN-{next_txn}",
                "merchant_id": merchant_id,
                "customer_id": random.choice(CUSTOMER_IDS),
                "amount": money(amount),
                "currency": cfg["currency"],
                "gateway": cfg["gateway"],
                "transaction_status": status,
                "transaction_time": ts(when),
                "settlement_id": settlement_id,
            })
            next_txn += 1

        # The settlement total is exactly the sum of its successful payments.
        totals[plan["settlement_id"]] = round(success_total, 2)

    # Today's transactions: captured, but not settled yet.
    for item in UNSETTLED_PLAN:
        cfg = MERCHANTS[item["merchant_id"]]
        when = DEMO_TODAY + timedelta(hours=random.randint(9, 13),
                                      minutes=random.randint(0, 59))
        transactions.append({
            "transaction_id": f"TXN-{next_txn}",
            "merchant_id": item["merchant_id"],
            "customer_id": random.choice(CUSTOMER_IDS),
            "amount": money(random_amount(item["merchant_id"])),
            "currency": cfg["currency"],
            "gateway": cfg["gateway"],
            "transaction_status": item["status"],
            "transaction_time": ts(when),
            "settlement_id": "",  # empty on purpose: not batched yet
        })
        next_txn += 1

    return transactions, totals


# ---------------------------------------------------------------------------
# 5. BUILD: SETTLEMENTS
# ---------------------------------------------------------------------------

# Settlements that were never handed to the bank have no bank reference.
NO_BANK_REFERENCE = {"none"}


def build_settlements(totals):
    """Create one row per settlement batch."""
    settlements = []

    for plan in SETTLEMENT_PLAN:
        sid = plan["settlement_id"]
        number = int(sid.split("-")[1])          # "SET-005" -> 5
        created = plan["date"] + timedelta(hours=22, minutes=5)
        expected_payout = plan["date"] + timedelta(days=1)  # banks pay T+1

        # Only settlements that actually moved money get an actual payout date.
        if plan["status"] in ("SUCCESS", "PARTIAL"):
            actual_payout = day(expected_payout)
        else:
            actual_payout = ""

        bank_reference = "" if plan["bank"] in NO_BANK_REFERENCE else f"BANK-{number:03d}"

        settlements.append({
            "settlement_id": sid,
            "merchant_id": plan["merchant_id"],
            "settlement_date": ts(created),
            "total_amount": money(totals[sid]),
            "currency": MERCHANTS[plan["merchant_id"]]["currency"],
            "settlement_status": plan["status"],
            "expected_payout_date": day(expected_payout),
            "actual_payout_date": actual_payout,
            "bank_reference": bank_reference,
            "failure_reason": plan["failure_reason"],
        })

    return settlements


# ---------------------------------------------------------------------------
# 6. BUILD: GATEWAY LOGS
# ---------------------------------------------------------------------------


def build_gateway_logs(transactions):
    """
    Create the gateway's event trail.

    Two kinds of rows:
      * transaction events  -> PAYMENT_RECEIVED / PAYMENT_AUTHORIZED / PAYMENT_FAILED
      * settlement events   -> SETTLEMENT_CREATED / SETTLEMENT_SUBMITTED
    """
    rows = []

    # Which day had a gateway outage? (used to pick nastier error messages)
    outage_settlements = {p["settlement_id"] for p in SETTLEMENT_PLAN
                          if p["fail_profile"] == "gateway_outage"}
    plan_by_date_merchant = {(day(p["date"]), p["merchant_id"]): p
                             for p in SETTLEMENT_PLAN}

    # ---- 6a. one small trail per transaction ----------------------------
    for txn in transactions:
        received_at = datetime.strptime(txn["transaction_time"], "%Y-%m-%d %H:%M:%S")
        plan = plan_by_date_merchant.get((day(received_at), txn["merchant_id"]))
        sid = txn["settlement_id"]

        # Step 1: the gateway always receives the payment request.
        rows.append({
            "transaction_id": txn["transaction_id"], "settlement_id": sid,
            "gateway": txn["gateway"], "event_type": "PAYMENT_RECEIVED",
            "status": "RECEIVED", "timestamp": ts(received_at),
            "response_code": 200, "message": "Payment request received by gateway",
        })

        # Step 2: what happened next depends on the transaction status.
        decided_at = received_at + timedelta(seconds=random.randint(2, 45))

        if txn["transaction_status"] == "SUCCESS":
            rows.append({
                "transaction_id": txn["transaction_id"], "settlement_id": sid,
                "gateway": txn["gateway"], "event_type": "PAYMENT_AUTHORIZED",
                "status": "AUTHORIZED", "timestamp": ts(decided_at),
                "response_code": 201, "message": "Payment authorized by acquirer",
            })
        elif txn["transaction_status"] == "FAILED":
            in_outage = plan is not None and plan["settlement_id"] in outage_settlements
            pool = GATEWAY_OUTAGE_FAILURES if in_outage else DECLINE_FAILURES
            code, message = random.choice(pool)
            rows.append({
                "transaction_id": txn["transaction_id"], "settlement_id": "",
                "gateway": txn["gateway"], "event_type": "PAYMENT_FAILED",
                "status": "FAILED", "timestamp": ts(decided_at),
                "response_code": code, "message": message,
            })
        else:  # PENDING - the gateway is still waiting for an answer
            rows.append({
                "transaction_id": txn["transaction_id"], "settlement_id": "",
                "gateway": txn["gateway"], "event_type": "PAYMENT_RECEIVED",
                "status": "PENDING", "timestamp": ts(decided_at),
                "response_code": 202, "message": "Awaiting authorization from acquirer",
            })

    # ---- 6b. settlement-level events ------------------------------------
    for plan in SETTLEMENT_PLAN:
        sid = plan["settlement_id"]
        gateway = MERCHANTS[plan["merchant_id"]]["gateway"]
        created_at = plan["date"] + timedelta(hours=22, minutes=5)
        submitted_at = created_at + timedelta(minutes=25)

        if plan["gateway"] == "not_submitted":
            # Compliance hold: the batch exists but is never sent to the bank.
            rows.append({
                "transaction_id": "", "settlement_id": sid, "gateway": gateway,
                "event_type": "SETTLEMENT_CREATED", "status": "ON_HOLD",
                "timestamp": ts(created_at), "response_code": 202,
                "message": "Settlement created but held for compliance review; not submitted to bank",
            })
            continue

        rows.append({
            "transaction_id": "", "settlement_id": sid, "gateway": gateway,
            "event_type": "SETTLEMENT_CREATED", "status": "CREATED",
            "timestamp": ts(created_at), "response_code": 201,
            "message": f"Settlement batch created from {plan['n_success']} authorized transactions",
        })

        if plan["gateway"] == "submit_failed":
            # The gateway itself broke while submitting to the bank.
            rows.append({
                "transaction_id": "", "settlement_id": sid, "gateway": gateway,
                "event_type": "SETTLEMENT_SUBMITTED", "status": "FAILED",
                "timestamp": ts(submitted_at), "response_code": 500,
                "message": "Gateway internal error while submitting settlement file to bank",
            })
        else:
            rows.append({
                "transaction_id": "", "settlement_id": sid, "gateway": gateway,
                "event_type": "SETTLEMENT_SUBMITTED", "status": "SUBMITTED",
                "timestamp": ts(submitted_at), "response_code": 200,
                "message": "Settlement file submitted to bank for payout",
            })

    # Sort by time so the file reads like a real log, then number the rows.
    rows.sort(key=lambda r: (r["timestamp"], r["transaction_id"], r["settlement_id"]))
    for index, row in enumerate(rows, start=1):
        row["log_id"] = f"GWL-{index:05d}"

    # Put the columns in a friendly order.
    ordered = [{
        "log_id": r["log_id"], "transaction_id": r["transaction_id"],
        "settlement_id": r["settlement_id"], "gateway": r["gateway"],
        "event_type": r["event_type"], "status": r["status"],
        "timestamp": r["timestamp"], "response_code": r["response_code"],
        "message": r["message"],
    } for r in rows]
    return ordered


# ---------------------------------------------------------------------------
# 7. BUILD: BANK LOGS
# ---------------------------------------------------------------------------


def build_bank_logs(totals):
    """
    Create the bank's replies for each settlement.

    Each "story" below produces a different, easy-to-spot trail:
      completed        -> ACCEPTED, PROCESSING, COMPLETED
      partial          -> ACCEPTED, PROCESSING, COMPLETED (206, partial credit)
      rejected_funds   -> ACCEPTED, REJECTED (402)
      rejected_account -> REJECTED (400)
      timeout          -> ACCEPTED, TIMEOUT (504)
      stuck            -> ACCEPTED, PROCESSING (and nothing ever again)
      none             -> no rows at all (bank never received the settlement)
    """
    rows = []

    for plan in SETTLEMENT_PLAN:
        sid = plan["settlement_id"]
        story = plan["bank"]
        if story == "none":
            continue  # deliberately no bank records

        number = int(sid.split("-")[1])
        reference = f"BANK-{number:03d}"
        submitted_at = plan["date"] + timedelta(hours=22, minutes=30)
        next_morning = plan["date"] + timedelta(days=1, hours=10)

        def add(status, code, message, when):
            rows.append({
                "settlement_id": sid, "bank_reference": reference,
                "timestamp": ts(when), "status": status,
                "response_code": code, "message": message,
            })

        if story in ("completed", "partial", "rejected_funds", "timeout", "stuck"):
            add("ACCEPTED", 200, "Settlement file accepted by bank for processing", submitted_at)

        if story == "completed":
            add("PROCESSING", 202, "Payout batch queued in bank clearing system",
                submitted_at + timedelta(minutes=20))
            add("COMPLETED", 200, f"Payout of {money(totals[sid])} credited to merchant account",
                next_morning)

        elif story == "partial":
            paid = round(totals[sid] * 0.6, 2)
            add("PROCESSING", 202, "Payout batch queued in bank clearing system",
                submitted_at + timedelta(minutes=20))
            add("COMPLETED", 206,
                f"Partial payout: {money(paid)} of {money(totals[sid])} credited; "
                f"remaining credits failed beneficiary validation",
                next_morning)

        elif story == "rejected_funds":
            add("REJECTED", 402,
                "Payout rejected: insufficient balance in platform pool account",
                submitted_at + timedelta(minutes=35))

        elif story == "rejected_account":
            add("REJECTED", 400,
                "Payout rejected: beneficiary account number failed validation",
                submitted_at + timedelta(minutes=12))

        elif story == "timeout":
            add("TIMEOUT", 504, "Bank response timeout - no acknowledgement received",
                submitted_at + timedelta(minutes=31))

        elif story == "stuck":
            add("PROCESSING", 202,
                "Payout batch queued in bank clearing system - awaiting confirmation",
                submitted_at + timedelta(minutes=20))

    rows.sort(key=lambda r: (r["timestamp"], r["settlement_id"]))
    for index, row in enumerate(rows, start=1):
        row["bank_log_id"] = f"BNK-{index:04d}"

    ordered = [{
        "bank_log_id": r["bank_log_id"], "settlement_id": r["settlement_id"],
        "bank_reference": r["bank_reference"], "timestamp": r["timestamp"],
        "status": r["status"], "response_code": r["response_code"],
        "message": r["message"],
    } for r in rows]
    return ordered


# ---------------------------------------------------------------------------
# 8. BUILD: LEDGER
# ---------------------------------------------------------------------------
# The ledger is the accounting book.
#   SETTLEMENT_CREDIT -> we record that we OWE this money to the merchant
#   SETTLEMENT_DEBIT  -> we actually SENT the money out to the bank
#   REVERSAL          -> the payout failed, so we put the money back
#   ADJUSTMENT        -> a fee or correction
#
# `balance` is a running total per merchant: balance = balance + credit - debit.
# A healthy settlement ends with balance back where it started (we owed money,
# then we paid it). A broken one leaves money sitting in the balance.


def build_ledger(totals):
    """Create ledger rows, then compute the running balance per merchant."""
    entries = []

    for plan in SETTLEMENT_PLAN:
        sid = plan["settlement_id"]
        merchant_id = plan["merchant_id"]
        total = totals[sid]
        credit_at = plan["date"] + timedelta(hours=22, minutes=10)
        payout_at = plan["date"] + timedelta(days=1, hours=10, minutes=30)
        story = plan["ledger"]

        def add(entry_type, debit, credit, status, when, note):
            entries.append({
                "settlement_id": sid, "merchant_id": merchant_id,
                "entry_type": entry_type,
                "amount": money(max(debit, credit)),
                "debit": money(debit), "credit": money(credit),
                "status": status, "timestamp": ts(when), "description": note,
            })

        if story == "missing":
            # SC-07: on purpose, we write nothing for this settlement.
            continue

        if story == "mismatch":
            # SC-08: the credit is 1500.00 too low compared to the settlement.
            wrong = round(total - 1500.00, 2)
            add("SETTLEMENT_CREDIT", 0.0, wrong, "POSTED", credit_at,
                "Settlement amount payable to merchant")
            add("SETTLEMENT_DEBIT", total, 0.0, "POSTED", payout_at,
                "Payout transferred to merchant bank account")
            continue

        # Every other story starts by recording what we owe the merchant.
        credit_status = {"on_hold": "ON_HOLD", "pending": "PENDING",
                         "failed_no_payout": "FAILED"}.get(story, "POSTED")
        add("SETTLEMENT_CREDIT", 0.0, total, credit_status, credit_at,
            "Settlement amount payable to merchant")

        if story == "clean":
            add("SETTLEMENT_DEBIT", total, 0.0, "POSTED", payout_at,
                "Payout transferred to merchant bank account")

        elif story == "adjustment":
            # SC-12: full amount is paid out, then a platform fee is charged
            # as a separate ADJUSTMENT entry (so the payout still matches the
            # settlement total and the bank log - only the fee is extra).
            add("SETTLEMENT_DEBIT", total, 0.0, "POSTED", payout_at,
                "Payout transferred to merchant bank account")
            add("ADJUSTMENT", 250.00, 0.0, "POSTED",
                payout_at + timedelta(minutes=30),
                "Platform fee charged after payout")

        elif story == "partial":
            # SC-11: only part of the money actually left.
            add("SETTLEMENT_DEBIT", round(total * 0.6, 2), 0.0, "PARTIAL", payout_at,
                "Partial payout transferred; remainder still payable")

        elif story == "reversal":
            # SC-02 / SC-09: we tried to pay, the bank refused, we reversed it.
            add("SETTLEMENT_DEBIT", total, 0.0, "REVERSED", payout_at,
                "Payout attempt to merchant bank account")
            add("REVERSAL", 0.0, total, "POSTED", payout_at + timedelta(minutes=45),
                "Payout reversed after bank rejection; amount still payable")

        # "pending", "on_hold" and "failed_no_payout" stop after the credit:
        # the money never left, which is exactly what makes them detectable.

    # Sort by time so balances add up in the right order.
    entries.sort(key=lambda e: (e["timestamp"], e["settlement_id"], e["entry_type"]))

    balances = {merchant_id: 0.0 for merchant_id in MERCHANTS}
    ordered = []
    for index, entry in enumerate(entries, start=1):
        merchant_id = entry["merchant_id"]
        balances[merchant_id] += float(entry["credit"]) - float(entry["debit"])
        balances[merchant_id] = round(balances[merchant_id], 2)

        ordered.append({
            "ledger_id": f"LED-{index:04d}",
            "settlement_id": entry["settlement_id"],
            "merchant_id": merchant_id,
            "entry_type": entry["entry_type"],
            "amount": entry["amount"],
            "debit": entry["debit"],
            "credit": entry["credit"],
            "balance": money(balances[merchant_id]),
            "status": entry["status"],
            "timestamp": entry["timestamp"],
            "description": entry["description"],
        })
    return ordered


# ---------------------------------------------------------------------------
# 9. WRITE THE CSV FILES
# ---------------------------------------------------------------------------


def write_csv(filename, rows):
    """Save a list of dictionaries as a CSV file inside the data/ folder."""
    path = os.path.join(DATA_DIR, filename)
    columns = list(rows[0].keys())
    # newline="" is required on Windows, otherwise CSVs get blank lines.
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {filename:<20} {len(rows):>5} rows")
    return path


def main():
    random.seed(RANDOM_SEED)  # makes the output identical every run

    transactions, totals = build_transactions()
    settlements = build_settlements(totals)
    gateway_logs = build_gateway_logs(transactions)
    bank_logs = build_bank_logs(totals)
    ledger = build_ledger(totals)

    print("Generating SettleSherlock demo data (all values are synthetic)...")
    write_csv("transactions.csv", transactions)
    write_csv("settlements.csv", settlements)
    write_csv("gateway_logs.csv", gateway_logs)
    write_csv("bank_logs.csv", bank_logs)
    write_csv("ledger.csv", ledger)

    print("\nInvestigation scenarios built into this dataset:")
    for plan in SETTLEMENT_PLAN:
        print(f"  {plan['scenario']}  {plan['settlement_id']}  "
              f"{plan['status']:<11}{plan['note']}")

    print("\nDone. Next step:  python data/validate_data.py")


if __name__ == "__main__":
    main()
