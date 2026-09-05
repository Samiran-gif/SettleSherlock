"""Stage 5 of the pipeline: timeline reconstruction.

Builds a chronological narrative of the case from the normalized records plus
markers for the windows that pattern detection found interesting. Records
without a usable timestamp are not dropped — they are appended as
``timestamp: null`` events so the investigator can see what could not be
placed in time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from .config import InvestigationConfig
from .models import (
    DetectedPattern,
    Importance,
    Severity,
    TimelineEvent,
    Transaction,
    TransactionStatus,
    importance_for_severity,
    max_severity,
)

__all__ = ["build_timeline"]


#: Human-readable phrasing per canonical status.
_STATUS_PHRASE: dict[TransactionStatus, str] = {
    TransactionStatus.SUCCESS: "settled successfully",
    TransactionStatus.FAILED: "failed",
    TransactionStatus.PENDING: "was initiated and is still pending",
    TransactionStatus.REVERSED: "was reversed",
    TransactionStatus.REFUNDED: "was refunded",
    TransactionStatus.CANCELLED: "was cancelled",
    TransactionStatus.UNKNOWN: "was recorded with an unknown status",
}


def _describe(txn: Transaction) -> str:
    """One-line description of what a transaction record says happened."""
    amount = (
        f"{txn.amount} {txn.currency}".strip()
        if txn.amount is not None
        else "an unspecified amount"
    )
    route = ""
    if txn.sender or txn.receiver:
        route = f" from {txn.sender or 'unknown sender'} to {txn.receiver or 'unknown receiver'}"
    channel = ""
    if txn.gateway or txn.bank:
        channel = f" via {txn.gateway or txn.bank}"
    return f"{amount}{route}{channel} {_STATUS_PHRASE[txn.status]}"


def build_timeline(
    transactions: Sequence[Transaction],
    patterns: Sequence[DetectedPattern],
    config: InvestigationConfig | None = None,
) -> list[TimelineEvent]:
    """Return the chronological event list for the report.

    Importance is driven by the severity of the patterns a transaction takes
    part in, so the timeline highlights the records that matter instead of
    treating every row equally.
    """
    config = config or InvestigationConfig()

    # transaction_id -> severities of the patterns implicating it
    implications: dict[str, list[Severity]] = {}
    for pattern in patterns:
        for txn_id in pattern.transaction_ids:
            implications.setdefault(txn_id, []).append(pattern.severity)

    events: list[TimelineEvent] = []

    for txn in transactions:
        severities = implications.get(txn.transaction_id, [])
        importance = (
            importance_for_severity(max_severity(severities))
            if severities
            else Importance.LOW
        )
        related = sorted(
            {
                p.pattern_id
                for p in patterns
                if txn.transaction_id in p.transaction_ids
            }
        )
        events.append(
            TimelineEvent(
                timestamp=txn.timestamp,
                transaction_id=txn.transaction_id,
                event=_describe(txn),
                importance=importance,
                details={
                    "status": txn.status.value,
                    "amount": txn.amount,
                    "currency": txn.currency,
                    "sender": txn.sender,
                    "receiver": txn.receiver,
                    "gateway": txn.gateway,
                    "bank": txn.bank,
                    "reference_id": txn.reference_id,
                    "related_patterns": related,
                    "timestamp_missing": txn.timestamp is None,
                },
            )
        )

    # Window markers for patterns that describe an interval rather than a row.
    for pattern in patterns:
        start = pattern.metrics.get("window_start")
        if isinstance(start, datetime):
            events.append(
                TimelineEvent(
                    timestamp=start,
                    transaction_id=None,
                    event=f"[marker] {pattern.name} begins: {pattern.description}",
                    importance=importance_for_severity(pattern.severity),
                    details={
                        "pattern_id": pattern.pattern_id,
                        "window_end": pattern.metrics.get("window_end"),
                        "marker": True,
                    },
                )
            )

    stamped = sorted(
        (e for e in events if e.timestamp is not None),
        key=lambda e: (e.timestamp, e.transaction_id or ""),  # type: ignore[arg-type,return-value]
    )
    unstamped = sorted(
        (e for e in events if e.timestamp is None), key=lambda e: e.transaction_id or ""
    )
    ordered = stamped + unstamped

    if len(ordered) > config.max_timeline_events:
        # Keep the most important events when truncating, then re-sort.
        ranked = sorted(
            ordered,
            key=lambda e: (
                {Importance.HIGH: 0, Importance.MEDIUM: 1, Importance.LOW: 2}[e.importance],
            ),
        )[: config.max_timeline_events]
        kept = set(id(e) for e in ranked)
        ordered = [e for e in ordered if id(e) in kept]

    return ordered
