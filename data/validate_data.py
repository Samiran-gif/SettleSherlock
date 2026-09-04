"""
SettleSherlock - data validator
===============================

WHAT THIS SCRIPT DOES
---------------------
It opens the 5 CSV files in this folder and checks that they are healthy:

  1. every required column exists
  2. every transaction's settlement_id really exists in settlements.csv
  3. every settlement_id used by gateway_logs exists
  4. every settlement_id used by bank_logs exists
  5. every settlement_id used by ledger exists
  6. amount fields contain real numbers
  7. important id fields are not empty
  8. primary ids are unique (no duplicates)
  9. dates and timestamps are written in the right format
 10. each settlement total equals the sum of its SUCCESS transactions

HOW TO RUN IT
-------------
    python data/validate_data.py

WHAT YOU SHOULD SEE
-------------------
A short report ending in "Overall result: PASS".

NOTE ABOUT THE BROKEN SETTLEMENTS
---------------------------------
This dataset contains *deliberate* problems (missing ledger entries, bank
rejections, wrong amounts). Those are our demo puzzles, NOT data errors, so
they are listed separately at the bottom under "KNOWN INVESTIGATION
SCENARIOS". They do not make the validation fail.
"""

import csv
import os
import sys
from datetime import datetime

DATA_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# WHAT EACH FILE MUST LOOK LIKE
# ---------------------------------------------------------------------------
# required   -> columns that must be present
# primary    -> the id column that must be unique and never empty
# not_empty  -> columns that must always have a value
# numeric    -> columns that must contain a number
# datetimes  -> columns formatted "YYYY-MM-DD HH:MM:SS"
# dates      -> columns formatted "YYYY-MM-DD"
# optional_dates -> like dates, but may be empty

RULES = {
    "transactions.csv": {
        "label": "Transactions",
        "required": ["transaction_id", "merchant_id", "customer_id", "amount",
                     "currency", "gateway", "transaction_status",
                     "transaction_time", "settlement_id"],
        "primary": "transaction_id",
        "not_empty": ["transaction_id", "merchant_id", "customer_id",
                      "currency", "transaction_status"],
        "numeric": ["amount"],
        "datetimes": ["transaction_time"],
        "dates": [],
        "optional_dates": [],
    },
    "settlements.csv": {
        "label": "Settlements",
        "required": ["settlement_id", "merchant_id", "settlement_date",
                     "total_amount", "settlement_status",
                     "expected_payout_date", "actual_payout_date",
                     "bank_reference", "failure_reason"],
        "primary": "settlement_id",
        "not_empty": ["settlement_id", "merchant_id", "settlement_status"],
        "numeric": ["total_amount"],
        "datetimes": ["settlement_date"],
        "dates": ["expected_payout_date"],
        "optional_dates": ["actual_payout_date"],
    },
    "gateway_logs.csv": {
        "label": "Gateway logs",
        "required": ["log_id", "transaction_id", "gateway", "event_type",
                     "status", "timestamp", "response_code", "message"],
        "primary": "log_id",
        "not_empty": ["log_id", "gateway", "event_type", "status"],
        "numeric": ["response_code"],
        "datetimes": ["timestamp"],
        "dates": [],
        "optional_dates": [],
    },
    "bank_logs.csv": {
        "label": "Bank logs",
        "required": ["bank_log_id", "settlement_id", "bank_reference",
                     "timestamp", "status", "response_code", "message"],
        "primary": "bank_log_id",
        "not_empty": ["bank_log_id", "settlement_id", "bank_reference", "status"],
        "numeric": ["response_code"],
        "datetimes": ["timestamp"],
        "dates": [],
        "optional_dates": [],
    },
    "ledger.csv": {
        "label": "Ledger",
        "required": ["ledger_id", "settlement_id", "merchant_id", "entry_type",
                     "amount", "debit", "credit", "balance", "status",
                     "timestamp"],
        "primary": "ledger_id",
        "not_empty": ["ledger_id", "settlement_id", "merchant_id",
                      "entry_type", "status"],
        "numeric": ["amount", "debit", "credit", "balance"],
        "datetimes": ["timestamp"],
        "dates": [],
        "optional_dates": [],
    },
}

# Values we allow in the "status" style columns.
ALLOWED_TRANSACTION_STATUS = {"SUCCESS", "FAILED", "PENDING"}
ALLOWED_SETTLEMENT_STATUS = {"SUCCESS", "PROCESSING", "FAILED", "ON_HOLD", "PARTIAL"}


# ---------------------------------------------------------------------------
# SMALL HELPERS
# ---------------------------------------------------------------------------


def read_csv(filename):
    """Read a CSV into a list of dictionaries. Returns None if missing."""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def is_number(text):
    """True if the text can be read as a number, e.g. '2500.00'."""
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def is_datetime(text):
    """True if the text looks like '2026-09-01 10:15:00'."""
    try:
        datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        return True
    except (TypeError, ValueError):
        return False


def is_date(text):
    """True if the text looks like '2026-09-01'."""
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# CHECK ONE FILE ON ITS OWN
# ---------------------------------------------------------------------------


def check_file(filename, rows, rules):
    """
    Run every single-file check. Returns a list of problem messages
    (an empty list means the file is fine).
    """
    problems = []

    if rows is None:
        return [f"{filename} is missing. Run: python data/generate_data.py"]
    if not rows:
        return [f"{filename} has a header but no data rows."]

    columns = set(rows[0].keys())

    # Check 1: required columns exist.
    missing = [c for c in rules["required"] if c not in columns]
    if missing:
        problems.append(f"missing column(s): {', '.join(missing)}")
        return problems  # no point checking values if columns are absent

    seen_ids = set()
    for line_number, row in enumerate(rows, start=2):  # line 1 is the header
        # Check 7: important ids are not empty.
        for column in rules["not_empty"]:
            if not (row.get(column) or "").strip():
                problems.append(f"line {line_number}: '{column}' is empty")

        # Check 8: no duplicate primary ids.
        key = row[rules["primary"]]
        if key in seen_ids:
            problems.append(f"line {line_number}: duplicate {rules['primary']} '{key}'")
        seen_ids.add(key)

        # Check 6: amount / code fields are numbers.
        for column in rules["numeric"]:
            if not is_number(row[column]):
                problems.append(
                    f"line {line_number}: '{column}' is not a number "
                    f"(found '{row[column]}')")

        # Check 9: date formats.
        for column in rules["datetimes"]:
            if not is_datetime(row[column]):
                problems.append(
                    f"line {line_number}: '{column}' must look like "
                    f"'2026-09-01 10:15:00' (found '{row[column]}')")
        for column in rules["dates"]:
            if not is_date(row[column]):
                problems.append(
                    f"line {line_number}: '{column}' must look like "
                    f"'2026-09-01' (found '{row[column]}')")
        for column in rules["optional_dates"]:
            value = (row[column] or "").strip()
            if value and not is_date(value):
                problems.append(
                    f"line {line_number}: '{column}' must be empty or look like "
                    f"'2026-09-01' (found '{value}')")

    # File-specific value checks.
    if filename == "transactions.csv":
        for line_number, row in enumerate(rows, start=2):
            if row["transaction_status"] not in ALLOWED_TRANSACTION_STATUS:
                problems.append(
                    f"line {line_number}: unknown transaction_status "
                    f"'{row['transaction_status']}'")
    if filename == "settlements.csv":
        for line_number, row in enumerate(rows, start=2):
            if row["settlement_status"] not in ALLOWED_SETTLEMENT_STATUS:
                problems.append(
                    f"line {line_number}: unknown settlement_status "
                    f"'{row['settlement_status']}'")

    return problems


# ---------------------------------------------------------------------------
# CHECK HOW THE FILES CONNECT TO EACH OTHER
# ---------------------------------------------------------------------------


def check_relationships(data):
    """Make sure ids used in one file really exist in the other file."""
    problems = []

    settlement_ids = {r["settlement_id"] for r in data["settlements.csv"]}
    transaction_ids = {r["transaction_id"] for r in data["transactions.csv"]}
    merchant_of_settlement = {r["settlement_id"]: r["merchant_id"]
                              for r in data["settlements.csv"]}

    # Check 2: transactions -> settlements (empty settlement_id is allowed:
    # it means the payment has not been batched into a settlement yet).
    for row in data["transactions.csv"]:
        sid = (row["settlement_id"] or "").strip()
        if sid and sid not in settlement_ids:
            problems.append(
                f"transactions.csv: {row['transaction_id']} points to "
                f"settlement '{sid}' which does not exist")

    # Check 3: gateway logs -> transactions and settlements
    for row in data["gateway_logs.csv"]:
        txn = (row.get("transaction_id") or "").strip()
        sid = (row.get("settlement_id") or "").strip()
        if txn and txn not in transaction_ids:
            problems.append(
                f"gateway_logs.csv: {row['log_id']} points to transaction "
                f"'{txn}' which does not exist")
        if sid and sid not in settlement_ids:
            problems.append(
                f"gateway_logs.csv: {row['log_id']} points to settlement "
                f"'{sid}' which does not exist")
        if not txn and not sid:
            problems.append(
                f"gateway_logs.csv: {row['log_id']} is not linked to any "
                f"transaction or settlement")

    # Check 4: bank logs -> settlements
    for row in data["bank_logs.csv"]:
        sid = (row["settlement_id"] or "").strip()
        if sid not in settlement_ids:
            problems.append(
                f"bank_logs.csv: {row['bank_log_id']} points to settlement "
                f"'{sid}' which does not exist")

    # Check 5: ledger -> settlements (and the merchant must be the same one)
    for row in data["ledger.csv"]:
        sid = (row["settlement_id"] or "").strip()
        if sid not in settlement_ids:
            problems.append(
                f"ledger.csv: {row['ledger_id']} points to settlement "
                f"'{sid}' which does not exist")
        elif row["merchant_id"] != merchant_of_settlement[sid]:
            problems.append(
                f"ledger.csv: {row['ledger_id']} says merchant "
                f"'{row['merchant_id']}' but {sid} belongs to "
                f"'{merchant_of_settlement[sid]}'")

    # Check 10: settlement total == sum of its SUCCESS transactions
    sums = {}
    for row in data["transactions.csv"]:
        sid = (row["settlement_id"] or "").strip()
        if sid and row["transaction_status"] == "SUCCESS":
            sums[sid] = round(sums.get(sid, 0.0) + float(row["amount"]), 2)

    for row in data["settlements.csv"]:
        sid = row["settlement_id"]
        expected = sums.get(sid, 0.0)
        actual = float(row["total_amount"])
        if abs(expected - actual) > 0.01:
            problems.append(
                f"settlements.csv: {sid} total_amount is {actual:.2f} but its "
                f"SUCCESS transactions add up to {expected:.2f}")

    # Every settlement should have at least one transaction behind it.
    for sid in settlement_ids:
        if sid not in sums:
            problems.append(
                f"settlements.csv: {sid} has no successful transactions")

    return problems


# ---------------------------------------------------------------------------
# FIND THE DELIBERATE PUZZLES (information only - never fails the run)
# ---------------------------------------------------------------------------


def find_known_scenarios(data):
    """
    Look for the intentional problems we planted for the AI team.
    This proves the demo puzzles are actually visible in the data.
    """
    findings = []

    settlements = data["settlements.csv"]
    ledger = data["ledger.csv"]
    bank_logs = data["bank_logs.csv"]
    gateway_logs = data["gateway_logs.csv"]

    ledger_by_settlement = {}
    for row in ledger:
        ledger_by_settlement.setdefault(row["settlement_id"], []).append(row)

    bank_by_settlement = {}
    for row in bank_logs:
        bank_by_settlement.setdefault(row["settlement_id"], []).append(row)

    submitted = {row.get("settlement_id") for row in gateway_logs
                 if row.get("event_type") == "SETTLEMENT_SUBMITTED"
                 and row.get("status") == "SUBMITTED"}

    for row in settlements:
        sid = row["settlement_id"]
        status = row["settlement_status"]
        reason = row["failure_reason"]
        total = float(row["total_amount"])
        entries = ledger_by_settlement.get(sid, [])
        bank_events = bank_by_settlement.get(sid, [])
        bank_statuses = [b["status"] for b in bank_events]

        if status in ("SUCCESS", "PARTIAL") and not entries:
            findings.append(f"{sid}: paid out but has NO ledger entries "
                            f"(SC-07 missing ledger credit)")

        credits = sum(float(e["credit"]) for e in entries
                      if e["entry_type"] == "SETTLEMENT_CREDIT")
        if entries and abs(credits - total) > 0.01 and status == "SUCCESS":
            findings.append(f"{sid}: ledger credit {credits:.2f} != settlement "
                            f"total {total:.2f} (SC-08 amount mismatch)")

        if any(e["entry_type"] == "REVERSAL" for e in entries):
            findings.append(f"{sid}: ledger contains a REVERSAL "
                            f"(SC-09 reversal after failed payout)")

        if "REJECTED" in bank_statuses:
            findings.append(f"{sid}: bank REJECTED the payout "
                            f"(SC-02 / SC-09 / reason={reason})")
        if "TIMEOUT" in bank_statuses:
            findings.append(f"{sid}: bank TIMEOUT (SC-03 / reason={reason})")
        if status == "PROCESSING" and "COMPLETED" not in bank_statuses \
                and "TIMEOUT" not in bank_statuses:
            findings.append(f"{sid}: stuck in PROCESSING, bank never confirmed "
                            f"(SC-04 / reason={reason})")
        if status == "ON_HOLD":
            findings.append(f"{sid}: ON_HOLD, never sent to bank "
                            f"(SC-05 / reason={reason})")
        if sid not in submitted and status not in ("ON_HOLD",):
            findings.append(f"{sid}: gateway never submitted it successfully "
                            f"(SC-06 / reason={reason})")
        if status == "PARTIAL":
            findings.append(f"{sid}: PARTIAL payout, merchant underpaid "
                            f"(SC-11 / reason={reason})")
        if any(e["entry_type"] == "ADJUSTMENT" for e in entries):
            findings.append(f"{sid}: ledger contains an ADJUSTMENT/fee (SC-12)")

    # SC-10: a settlement day where most transactions failed.
    per_day = {}
    for row in data["transactions.csv"]:
        key = (row["merchant_id"], row["transaction_time"][:10])
        bucket = per_day.setdefault(key, {"total": 0, "failed": 0})
        bucket["total"] += 1
        if row["transaction_status"] == "FAILED":
            bucket["failed"] += 1
    for (merchant, date), bucket in sorted(per_day.items()):
        if bucket["total"] >= 5 and bucket["failed"] / bucket["total"] >= 0.5:
            findings.append(
                f"{merchant} on {date}: {bucket['failed']} of {bucket['total']} "
                f"transactions FAILED (SC-10 mass transaction failure)")

    return findings


# ---------------------------------------------------------------------------
# THE REPORT
# ---------------------------------------------------------------------------


def main():
    print("## DATA VALIDATION\n")

    data = {name: read_csv(name) for name in RULES}
    all_problems = {}
    file_ok = True

    # --- per-file checks ---
    for filename, rules in RULES.items():
        problems = check_file(filename, data[filename], rules)
        all_problems[filename] = problems
        result = "PASS" if not problems else "FAIL"
        if problems:
            file_ok = False
        print(f"{rules['label']:<14}: {result}")

    # --- relationship checks (only possible if all files loaded) ---
    if any(data[name] is None for name in RULES) or not file_ok:
        print("\nRelationships : SKIPPED (fix the file errors above first)")
        relationship_problems = []
        relationships_ok = False
    else:
        relationship_problems = check_relationships(data)
        relationships_ok = not relationship_problems
        print(f"\nRelationships : {'PASS' if relationships_ok else 'FAIL'}")

    overall = file_ok and relationships_ok
    print(f"\nOverall result: {'PASS' if overall else 'FAIL'}")

    # --- explain anything that went wrong, in plain English ---
    if not overall:
        print("\n## PROBLEMS FOUND\n")
        for filename, problems in all_problems.items():
            if problems:
                print(f"{filename}:")
                for problem in problems[:20]:
                    print(f"  - {problem}")
                if len(problems) > 20:
                    print(f"  ...and {len(problems) - 20} more")
                print()
        if relationship_problems:
            print("relationships:")
            for problem in relationship_problems[:20]:
                print(f"  - {problem}")
            if len(relationship_problems) > 20:
                print(f"  ...and {len(relationship_problems) - 20} more")
        print("\nTip: re-create the data with  python data/generate_data.py")
        return 1

    # --- row counts, so you can see the size of the dataset ---
    print("\n## ROW COUNTS\n")
    for filename in RULES:
        print(f"{filename:<20} {len(data[filename]):>5} rows")

    # --- the deliberate puzzles ---
    print("\n## KNOWN INVESTIGATION SCENARIOS DETECTED")
    print("(these are intentional demo puzzles, not data errors)\n")
    for finding in find_known_scenarios(data):
        print(f"  - {finding}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
