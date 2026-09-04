"""Stage 2 of the pipeline: deterministic statistical analysis.

Nothing in this module is heuristic prose — it produces reproducible numbers
that the pattern detectors and the confidence scorer consume. All money is
handled as :class:`~decimal.Decimal`; only derived statistics are floats.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence

from .models import Transaction, TransactionStatus

__all__ = [
    "AmountStats",
    "TimingStats",
    "TransactionAnalysis",
    "analyze_transactions",
    "robust_z_scores",
    "sliding_window_peak",
]

#: Consistency constant that puts a MAD-based score on a normal-z-like scale.
_MAD_SCALE = 0.6745


@dataclass(slots=True)
class AmountStats:
    """Distribution summary for the transaction amounts in a case."""

    count: int = 0
    total: Decimal = Decimal("0")
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    mean: float | None = None
    median: float | None = None
    stdev: float | None = None
    mad: float | None = None
    round_number_count: int = 0
    currencies: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "total": float(self.total),
            "minimum": float(self.minimum) if self.minimum is not None else None,
            "maximum": float(self.maximum) if self.maximum is not None else None,
            "mean": round(self.mean, 2) if self.mean is not None else None,
            "median": round(self.median, 2) if self.median is not None else None,
            "stdev": round(self.stdev, 2) if self.stdev is not None else None,
            "mad": round(self.mad, 2) if self.mad is not None else None,
            "round_number_count": self.round_number_count,
            "currencies": dict(sorted(self.currencies.items())),
        }


@dataclass(slots=True)
class TimingStats:
    """Temporal summary: span, gaps and per-hour distribution."""

    first_event: datetime | None = None
    last_event: datetime | None = None
    span_seconds: float | None = None
    median_gap_seconds: float | None = None
    min_gap_seconds: float | None = None
    hour_histogram: dict[int, int] = field(default_factory=dict)
    missing_timestamps: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "first_event": self.first_event.isoformat() if self.first_event else None,
            "last_event": self.last_event.isoformat() if self.last_event else None,
            "span_seconds": self.span_seconds,
            "median_gap_seconds": self.median_gap_seconds,
            "min_gap_seconds": self.min_gap_seconds,
            "hour_histogram": {str(k): v for k, v in sorted(self.hour_histogram.items())},
            "missing_timestamps": self.missing_timestamps,
        }


@dataclass(slots=True)
class TransactionAnalysis:
    """Everything the deterministic analysis stage computed."""

    transaction_count: int
    amounts: AmountStats
    timing: TimingStats
    status_counts: dict[str, int] = field(default_factory=dict)
    failure_rate: float = 0.0
    reversal_rate: float = 0.0
    amount_outliers: dict[str, float] = field(default_factory=dict)
    per_sender_counts: dict[str, int] = field(default_factory=dict)
    per_receiver_counts: dict[str, int] = field(default_factory=dict)
    per_gateway_status: dict[str, dict[str, int]] = field(default_factory=dict)
    per_bank_status: dict[str, dict[str, int]] = field(default_factory=dict)
    per_account_currencies: dict[str, list[str]] = field(default_factory=dict)
    reference_groups: dict[str, list[str]] = field(default_factory=dict)
    duplicate_id_groups: dict[str, list[int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_count": self.transaction_count,
            "amounts": self.amounts.to_dict(),
            "timing": self.timing.to_dict(),
            "status_counts": dict(sorted(self.status_counts.items())),
            "failure_rate": round(self.failure_rate, 3),
            "reversal_rate": round(self.reversal_rate, 3),
            "amount_outliers": {k: round(v, 2) for k, v in self.amount_outliers.items()},
            "distinct_senders": len(self.per_sender_counts),
            "distinct_receivers": len(self.per_receiver_counts),
            "gateways": sorted(self.per_gateway_status.keys()),
            "banks": sorted(self.per_bank_status.keys()),
        }


# --------------------------------------------------------------------------- #
# Reusable numeric helpers
# --------------------------------------------------------------------------- #
def robust_z_scores(values: Sequence[float]) -> list[float]:
    """Return MAD-based robust z-scores for ``values``.

    The median absolute deviation is preferred over the standard deviation
    because a single large fraudulent amount would otherwise inflate sigma and
    hide itself. Falls back to a classic z-score when the MAD is zero.
    """
    if len(values) < 3:
        return [0.0] * len(values)

    median = statistics.median(values)
    deviations = [abs(v - median) for v in values]
    mad = statistics.median(deviations)

    if mad > 0:
        return [_MAD_SCALE * (v - median) / mad for v in values]

    try:
        sigma = statistics.stdev(values)
    except statistics.StatisticsError:
        return [0.0] * len(values)
    if sigma == 0:
        return [0.0] * len(values)
    mean = statistics.fmean(values)
    return [(v - mean) / sigma for v in values]


def sliding_window_peak(
    timestamps: Sequence[datetime], window: timedelta
) -> tuple[int, datetime | None, datetime | None]:
    """Find the densest ``window`` in ``timestamps``.

    Returns ``(peak_count, window_start, window_end)``. Runs in O(n) over the
    sorted input using a two-pointer scan.
    """
    ordered = sorted(timestamps)
    if not ordered:
        return 0, None, None

    best_count = 0
    best_start: datetime | None = None
    best_end: datetime | None = None
    left = 0
    for right, current in enumerate(ordered):
        while current - ordered[left] > window:
            left += 1
        count = right - left + 1
        if count > best_count:
            best_count = count
            best_start = ordered[left]
            best_end = current
    return best_count, best_start, best_end


def _is_round_number(amount: Decimal) -> bool:
    """True when an amount looks deliberately rounded (e.g. 500, 10 000)."""
    magnitude = abs(amount)
    if magnitude == 0:
        return False
    for step in (Decimal("1000"), Decimal("500"), Decimal("100")):
        if magnitude >= step and magnitude % step == 0:
            return True
    return False


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def analyze_transactions(transactions: Sequence[Transaction]) -> TransactionAnalysis:
    """Compute the deterministic statistical profile of a case."""
    amounts_by_txn: list[tuple[str, Decimal]] = [
        (t.transaction_id, t.amount) for t in transactions if t.amount is not None
    ]
    numeric = [float(a) for _, a in amounts_by_txn]

    amount_stats = AmountStats(count=len(amounts_by_txn))
    if amounts_by_txn:
        values = [a for _, a in amounts_by_txn]
        amount_stats.total = sum(values, Decimal("0"))
        amount_stats.minimum = min(values)
        amount_stats.maximum = max(values)
        amount_stats.mean = statistics.fmean(numeric)
        amount_stats.median = statistics.median(numeric)
        amount_stats.stdev = statistics.stdev(numeric) if len(numeric) > 1 else 0.0
        median = amount_stats.median
        amount_stats.mad = statistics.median([abs(v - median) for v in numeric])
        amount_stats.round_number_count = sum(1 for v in values if _is_round_number(v))
    amount_stats.currencies = dict(
        Counter(t.currency for t in transactions if t.currency)
    )

    # -- outliers --------------------------------------------------------- #
    outliers: dict[str, float] = {}
    if len(numeric) >= 5:
        for (txn_id, _), score in zip(amounts_by_txn, robust_z_scores(numeric)):
            outliers[txn_id] = score

    # -- timing ----------------------------------------------------------- #
    stamped = sorted((t.timestamp for t in transactions if t.timestamp is not None))
    timing = TimingStats(missing_timestamps=sum(1 for t in transactions if t.timestamp is None))
    if stamped:
        timing.first_event = stamped[0]
        timing.last_event = stamped[-1]
        timing.span_seconds = (stamped[-1] - stamped[0]).total_seconds()
        gaps = [
            (b - a).total_seconds() for a, b in zip(stamped, stamped[1:])
        ]
        if gaps:
            timing.median_gap_seconds = statistics.median(gaps)
            timing.min_gap_seconds = min(gaps)
        timing.hour_histogram = dict(Counter(ts.hour for ts in stamped))

    # -- statuses --------------------------------------------------------- #
    status_counts = Counter(t.status.value for t in transactions)
    total = len(transactions) or 1
    failure_rate = status_counts.get(TransactionStatus.FAILED.value, 0) / total
    reversal_rate = (
        status_counts.get(TransactionStatus.REVERSED.value, 0)
        + status_counts.get(TransactionStatus.REFUNDED.value, 0)
    ) / total

    # -- per-entity rollups ----------------------------------------------- #
    per_gateway: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_bank: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_account_currencies: dict[str, set[str]] = defaultdict(set)
    reference_groups: dict[str, list[str]] = defaultdict(list)
    id_groups: dict[str, list[int]] = defaultdict(list)

    for txn in transactions:
        if txn.gateway:
            per_gateway[txn.gateway][txn.status.value] += 1
        if txn.bank:
            per_bank[txn.bank][txn.status.value] += 1
        account = txn.account_id or txn.sender
        if account and txn.currency:
            per_account_currencies[account].add(txn.currency)
        if txn.reference_id:
            reference_groups[txn.reference_id].append(txn.transaction_id)
        if not txn.synthetic_id:
            id_groups[txn.transaction_id].append(txn.index)

    return TransactionAnalysis(
        transaction_count=len(transactions),
        amounts=amount_stats,
        timing=timing,
        status_counts=dict(status_counts),
        failure_rate=failure_rate,
        reversal_rate=reversal_rate,
        amount_outliers=outliers,
        per_sender_counts=dict(Counter(t.sender for t in transactions if t.sender)),
        per_receiver_counts=dict(Counter(t.receiver for t in transactions if t.receiver)),
        per_gateway_status={k: dict(v) for k, v in per_gateway.items()},
        per_bank_status={k: dict(v) for k, v in per_bank.items()},
        per_account_currencies={k: sorted(v) for k, v in per_account_currencies.items()},
        reference_groups={k: v for k, v in reference_groups.items() if len(v) > 1},
        duplicate_id_groups={k: v for k, v in id_groups.items() if len(v) > 1},
    )
