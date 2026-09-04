from typing import Any

from agent.service import run_investigation


def build_ai_evidence(transaction_id: str, records: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Gateway/Bank/Ledger records into one logical transaction."""

    available = {
        source: record
        for source, record in records.items()
        if record is not None
    }

    if not available:
        return []

    # Use the earliest available record as the base transaction.
    base = min(
        available.values(),
        key=lambda record: record.timestamp,
    )

    # Build one logical transaction instead of treating each system record
    # as a separate transaction.
    evidence = {
        "transaction_id": transaction_id,
        "amount": base.amount,
        "timestamp": base.timestamp.isoformat(),
        "status": base.status,
        "metadata": {
            "source_system": "settlement_trace",
            "systems_present": list(available.keys()),
            "system_records": {},
        },
    }

    for source, record in available.items():
        evidence["metadata"]["system_records"][source] = {
            "amount": record.amount,
            "status": record.status,
            "timestamp": record.timestamp.isoformat(),
            "reference": record.reference,
        }

    return [evidence]


def investigate_with_ai(
    transaction_id: str,
    records: dict[str, Any],
) -> dict[str, Any]:
    """Run Investigation AI against the transaction's system records."""

    evidence = build_ai_evidence(transaction_id, records)

    if not evidence:
        return {
            "transaction_id": transaction_id,
            "error": "No investigation evidence found",
        }

    result = run_investigation(evidence)

    return {
        "transaction_id": transaction_id,
        "agent": result,
    }