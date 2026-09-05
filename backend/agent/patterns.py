"""Stage 4 of the pipeline: deterministic pattern detection.

Each detector is a pure function of ``(transactions, analysis, graph, config)``
and returns zero or more :class:`~agent.models.DetectedPattern` objects. All
confidences here are derived from measured quantities (counts, ratios,
z-scores) — no language model is involved, which is what allows the report to
label these findings as calculated facts rather than interpretation.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Callable, Sequence

from .analyzer import TransactionAnalysis, sliding_window_peak
from .config import InvestigationConfig
from .models import (
    DetectedPattern,
    EvidenceGrade,
    Severity,
    Transaction,
    TransactionStatus,
    severity_rank,
)
from .relationships import Edge, EntityGraph

__all__ = ["detect_patterns", "DETECTORS"]

#: Regulatory / reporting thresholds that structuring typically hides under.
_STRUCTURING_THRESHOLDS: tuple[Decimal, ...] = (
    Decimal("10000"),
    Decimal("50000"),
    Decimal("100000"),
    Decimal("1000000"),
)

Detector = Callable[
    [Sequence[Transaction], TransactionAnalysis, EntityGraph, InvestigationConfig],
    list[DetectedPattern],
]


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _clamp(value: float, low: float = 0.05, high: float = 0.98) -> float:
    return max(low, min(high, value))


def _sorted_by_time(transactions: Sequence[Transaction]) -> list[Transaction]:
    """Chronological order; records without a timestamp sort last, stably."""
    stamped = sorted(
        (t for t in transactions if t.timestamp is not None), key=lambda t: t.timestamp  # type: ignore[arg-type]
    )
    unstamped = [t for t in transactions if t.timestamp is None]
    return stamped + unstamped


def _amount_key(txn: Transaction) -> tuple[str, str, str, str]:
    """Group key for 'the same payment attempted again'."""
    return (
        txn.sender or "?",
        txn.receiver or "?",
        str(txn.amount) if txn.amount is not None else "?",
        txn.currency or "?",
    )


def _seconds_between(a: Transaction, b: Transaction) -> float | None:
    if a.timestamp is None or b.timestamp is None:
        return None
    return abs((b.timestamp - a.timestamp).total_seconds())


def _entities(transactions: Sequence[Transaction]) -> list[str]:
    values: list[str] = []
    for txn in transactions:
        for value in (txn.sender, txn.receiver, txn.gateway, txn.bank):
            if value and value not in values:
                values.append(value)
    return values


def _is_subpath(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    """True when ``short`` is a contiguous run of nodes inside ``long``."""
    if len(short) >= len(long):
        return False
    return any(
        long[offset : offset + len(short)] == short
        for offset in range(len(long) - len(short) + 1)
    )


def _relative_gap(first: Decimal, second: Decimal) -> float:
    """Relative difference between two amounts (0.0 when identical)."""
    reference = max(abs(first), abs(second))
    if reference == 0:
        return 0.0
    return float(abs(first - second) / reference)


# --------------------------------------------------------------------------- #
# Detectors: duplication
# --------------------------------------------------------------------------- #
def detect_reference_collisions(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Multiple records sharing one reference / idempotency key.

    Two successful settlements under one idempotency key is a double charge;
    a mixed-status group is instead a state-consistency problem.
    """
    patterns: list[DetectedPattern] = []
    by_reference: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.reference_id:
            by_reference[txn.reference_id].append(txn)

    for reference, group in sorted(by_reference.items()):
        distinct_ids = {t.transaction_id for t in group}
        if len(group) < 2 or len(distinct_ids) < 2:
            continue

        successes = [t for t in group if t.status is TransactionStatus.SUCCESS]
        statuses = sorted({t.status.value for t in group})

        if len(successes) >= 2:
            settled = sum((t.amount or Decimal("0")) for t in successes)
            patterns.append(
                DetectedPattern(
                    pattern_id="duplicate_settlement_reference",
                    name="Duplicate settlement under one reference",
                    description=(
                        f"Reference {reference!r} settled {len(successes)} times "
                        f"({', '.join(t.transaction_id for t in successes)}), "
                        f"moving {settled} {successes[0].currency or ''}".strip()
                    ),
                    severity=Severity.CRITICAL,
                    confidence=_clamp(0.80 + 0.05 * (len(successes) - 2)),
                    transaction_ids=[t.transaction_id for t in successes],
                    entities=_entities(successes),
                    metrics={
                        "reference_id": reference,
                        "successful_settlements": len(successes),
                        "total_settled": settled,
                        "duplicate_excess": settled - (successes[0].amount or Decimal("0")),
                    },
                    grade=EvidenceGrade.CONFIRMED_FACT,
                )
            )
        elif len(statuses) > 1:
            patterns.append(
                DetectedPattern(
                    pattern_id="shared_reference_mixed_state",
                    name="Shared reference with conflicting states",
                    description=(
                        f"Reference {reference!r} spans {len(group)} records with "
                        f"differing statuses ({', '.join(statuses)})"
                    ),
                    severity=Severity.MEDIUM,
                    confidence=0.65,
                    transaction_ids=sorted(distinct_ids),
                    entities=_entities(group),
                    metrics={"reference_id": reference, "statuses": statuses},
                    grade=EvidenceGrade.CONFIRMED_FACT,
                )
            )
    return patterns


def detect_near_duplicates(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Identical party/amount/currency transfers inside a short window."""
    patterns: list[DetectedPattern] = []
    window = config.duplicate_window_seconds

    groups: dict[tuple[str, str, str, str], list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.amount is None or not txn.is_value_moving:
            continue
        groups[_amount_key(txn)].append(txn)

    for key, group in groups.items():
        if len(group) < 2:
            continue
        ordered = _sorted_by_time(group)

        # Collect maximal clusters whose consecutive gaps stay inside window.
        cluster: list[Transaction] = [ordered[0]]
        clusters: list[list[Transaction]] = []
        for previous, current in zip(ordered, ordered[1:]):
            gap = _seconds_between(previous, current)
            # A missing timestamp cannot be excluded from the window, so the
            # pair is kept but the confidence is reduced further below.
            if gap is None or gap <= window:
                cluster.append(current)
            else:
                clusters.append(cluster)
                cluster = [current]
        clusters.append(cluster)

        for cluster in clusters:
            if len(cluster) < 2:
                continue
            gaps = [
                g
                for g in (
                    _seconds_between(a, b) for a, b in zip(cluster, cluster[1:])
                )
                if g is not None
            ]
            timing_known = len(gaps) == len(cluster) - 1
            tightness = 1.0 - (min(gaps) / window if gaps and window else 0.0)
            confidence = _clamp(0.60 + 0.20 * tightness + 0.05 * (len(cluster) - 2))
            if not timing_known:
                confidence = _clamp(confidence - 0.25)

            patterns.append(
                DetectedPattern(
                    pattern_id="near_duplicate_transaction",
                    name="Near-duplicate transactions",
                    description=(
                        f"{len(cluster)} transfers of {key[2]} {key[3]} from {key[0]} "
                        f"to {key[1]}"
                        + (
                            f" within {max(gaps):.0f}s"
                            if gaps
                            else " with incomplete timestamps"
                        )
                    ),
                    severity=Severity.HIGH,
                    confidence=confidence,
                    transaction_ids=[t.transaction_id for t in cluster],
                    entities=_entities(cluster),
                    metrics={
                        "cluster_size": len(cluster),
                        "min_gap_seconds": min(gaps) if gaps else None,
                        "max_gap_seconds": max(gaps) if gaps else None,
                        "window_seconds": window,
                        "timestamps_complete": timing_known,
                        "amount": key[2],
                        "currency": key[3],
                    },
                    grade=(
                        EvidenceGrade.CONFIRMED_FACT
                        if timing_known
                        else EvidenceGrade.STRONG_EVIDENCE
                    ),
                )
            )
    return patterns


def detect_repeated_amounts(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """The same amount moving between the same parties many times.

    Spread out over time this is often legitimate (subscriptions, payroll),
    hence the modest severity — it is reported so a human can rule it in.
    """
    patterns: list[DetectedPattern] = []
    groups: dict[tuple[str, str, str, str], list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.amount is None:
            continue
        groups[_amount_key(txn)].append(txn)

    for key, group in groups.items():
        if len(group) < config.repeat_min_count:
            continue
        ordered = _sorted_by_time(group)
        gaps = [
            g for g in (_seconds_between(a, b) for a, b in zip(ordered, ordered[1:])) if g
        ]
        regular = False
        if len(gaps) >= 2:
            spread = max(gaps) - min(gaps)
            regular = spread <= 0.1 * max(gaps)

        patterns.append(
            DetectedPattern(
                pattern_id="repeated_identical_amount",
                name="Repeated identical amount",
                description=(
                    f"{len(group)} transfers of exactly {key[2]} {key[3]} from "
                    f"{key[0]} to {key[1]}"
                    + (" on a fixed interval (looks scheduled)" if regular else "")
                ),
                severity=Severity.LOW if regular else Severity.MEDIUM,
                confidence=_clamp(0.45 + 0.05 * (len(group) - config.repeat_min_count)),
                transaction_ids=[t.transaction_id for t in ordered],
                entities=_entities(group),
                metrics={
                    "occurrences": len(group),
                    "amount": key[2],
                    "currency": key[3],
                    "regular_interval": regular,
                    "median_gap_seconds": (
                        sorted(gaps)[len(gaps) // 2] if gaps else None
                    ),
                },
                grade=EvidenceGrade.CONFIRMED_FACT,
            )
        )
    return patterns


# --------------------------------------------------------------------------- #
# Detectors: velocity and amounts
# --------------------------------------------------------------------------- #
def detect_bursts(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Unusual concentration of outbound activity by a single account.

    Burst is evaluated per sender rather than case-wide: many *different*
    senders transacting at once is a fan-in, not a burst, and reporting it as
    one produces noise on any busy merchant.
    """
    patterns: list[DetectedPattern] = []
    window = timedelta(seconds=config.burst_window_seconds)

    def evaluate(scope: str, subset: Sequence[Transaction]) -> DetectedPattern | None:
        stamps = [t.timestamp for t in subset if t.timestamp is not None]
        if len(stamps) < config.burst_min_count:
            return None
        peak, start, end = sliding_window_peak(stamps, window)
        if peak < config.burst_min_count:
            return None

        span = (max(stamps) - min(stamps)).total_seconds()
        # Expected count per window if the same volume were spread evenly.
        windows = max(1.0, span / config.burst_window_seconds)
        expected = len(stamps) / windows
        multiple = peak / expected if expected else float(peak)
        # The density comparison needs activity outside the burst to compare
        # against. When the whole case fits in a few windows, the absolute rate
        # is the finding and the ratio test is meaningless.
        baseline_available = span > 3 * config.burst_window_seconds
        if baseline_available and multiple < config.burst_density_multiple:
            return None

        in_window = [
            t
            for t in subset
            if t.timestamp is not None and start is not None and end is not None
            and start <= t.timestamp <= end
        ]
        return DetectedPattern(
            pattern_id="transaction_burst",
            name="Transaction burst",
            description=(
                f"{peak} transactions {scope} inside {config.burst_window_seconds:.0f}s "
                f"({multiple:.1f}x the expected density for this case)"
            ),
            severity=Severity.HIGH if peak >= config.burst_min_count * 2 else Severity.MEDIUM,
            confidence=_clamp(0.50 + 0.06 * (peak - config.burst_min_count) + 0.03 * multiple),
            transaction_ids=[t.transaction_id for t in in_window],
            entities=_entities(in_window),
            metrics={
                "scope": scope,
                "peak_count": peak,
                "window_seconds": config.burst_window_seconds,
                "expected_count_per_window": round(expected, 2),
                "density_multiple": round(multiple, 2),
                "window_start": start,
                "window_end": end,
            },
            grade=EvidenceGrade.CONFIRMED_FACT,
        )

    by_sender: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.sender:
            by_sender[txn.sender].append(txn)

    for sender, subset in sorted(by_sender.items()):
        found = evaluate(f"from {sender}", subset)
        if found:
            patterns.append(found)

    return patterns


def detect_amount_anomalies(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Amounts that do not belong to the distribution of the rest of the case."""
    patterns: list[DetectedPattern] = []
    median = analysis.amounts.median
    by_id = {t.transaction_id: t for t in transactions}

    flagged: dict[str, dict[str, float]] = {}
    for txn_id, score in analysis.amount_outliers.items():
        # Only upside outliers matter: an unusually *small* transfer carries no
        # financial exposure, and flagging it would bury the real signal.
        if score >= config.amount_outlier_z:
            flagged[txn_id] = {"robust_z": score}

    if median and median > 0:
        for txn in transactions:
            if txn.amount is None:
                continue
            ratio = float(txn.amount) / median
            if ratio >= config.amount_ratio_threshold:
                flagged.setdefault(txn.transaction_id, {})["median_ratio"] = ratio

    for txn_id, metrics in sorted(flagged.items()):
        txn = by_id.get(txn_id)
        if txn is None:
            continue
        z = abs(metrics.get("robust_z", 0.0))
        ratio = metrics.get("median_ratio")
        patterns.append(
            DetectedPattern(
                pattern_id="amount_outlier",
                name="Abnormal transaction amount",
                description=(
                    f"{txn.transaction_id} moves {txn.amount} {txn.currency or ''} — "
                    + (f"{ratio:.1f}x the case median" if ratio else f"robust z={z:.1f}")
                ).strip(),
                severity=Severity.HIGH if (ratio or 0) >= 20 or z >= 8 else Severity.MEDIUM,
                confidence=_clamp(0.45 + 0.04 * z + (0.02 * ratio if ratio else 0.0)),
                transaction_ids=[txn.transaction_id],
                entities=_entities([txn]),
                metrics={
                    "amount": txn.amount,
                    "case_median": median,
                    "sample_size": analysis.amounts.count,
                    **{k: round(v, 2) for k, v in metrics.items()},
                },
                grade=EvidenceGrade.STRONG_EVIDENCE,
            )
        )
    return patterns


def detect_structuring(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Repeated transfers sitting just below a reporting threshold."""
    patterns: list[DetectedPattern] = []
    by_sender: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.sender and txn.amount is not None and txn.amount > 0:
            by_sender[txn.sender].append(txn)

    for sender, subset in sorted(by_sender.items()):
        for threshold in _STRUCTURING_THRESHOLDS:
            lower = threshold * (Decimal("1") - Decimal(str(config.structuring_band)))
            band = [t for t in subset if lower <= (t.amount or Decimal("0")) < threshold]
            if len(band) < config.structuring_min_count:
                continue
            total = sum((t.amount or Decimal("0")) for t in band)
            if total < threshold:
                continue
            patterns.append(
                DetectedPattern(
                    pattern_id="threshold_structuring",
                    name="Possible threshold structuring",
                    description=(
                        f"{sender} sent {len(band)} transfers of {lower}–{threshold} "
                        f"totalling {total}, each individually under the {threshold} "
                        f"reporting threshold"
                    ),
                    severity=Severity.HIGH,
                    confidence=_clamp(
                        0.45 + 0.07 * (len(band) - config.structuring_min_count)
                    ),
                    transaction_ids=[t.transaction_id for t in _sorted_by_time(band)],
                    entities=_entities(band),
                    metrics={
                        "threshold": threshold,
                        "band_lower_bound": lower,
                        "transfers_in_band": len(band),
                        "total_in_band": total,
                    },
                    grade=EvidenceGrade.STRONG_EVIDENCE,
                )
            )
            break  # Report the tightest matching threshold only.
    return patterns


def detect_off_hours_activity(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Activity concentrated in the overnight window (UTC)."""
    stamped = [t for t in transactions if t.timestamp is not None]
    if len(stamped) < config.off_hours_min_count:
        return []
    off_hours = [
        t
        for t in stamped
        if config.off_hours_start <= t.timestamp.hour <= config.off_hours_end  # type: ignore[union-attr]
    ]
    ratio = len(off_hours) / len(stamped)
    if len(off_hours) < config.off_hours_min_count or ratio < config.off_hours_min_ratio:
        return []
    return [
        DetectedPattern(
            pattern_id="off_hours_activity",
            name="Off-hours activity concentration",
            description=(
                f"{len(off_hours)} of {len(stamped)} transactions ({ratio:.0%}) occurred "
                f"between {config.off_hours_start:02d}:00 and {config.off_hours_end:02d}:59 UTC"
            ),
            severity=Severity.LOW,
            confidence=_clamp(0.30 + 0.35 * ratio),
            transaction_ids=[t.transaction_id for t in _sorted_by_time(off_hours)],
            entities=_entities(off_hours),
            metrics={
                "off_hours_count": len(off_hours),
                "total_stamped": len(stamped),
                "ratio": round(ratio, 3),
                "hour_histogram": analysis.timing.hour_histogram,
            },
            grade=EvidenceGrade.CONFIRMED_FACT,
        )
    ]


# --------------------------------------------------------------------------- #
# Detectors: state machine consistency
# --------------------------------------------------------------------------- #
def detect_retry_sequences(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """``failed → success`` sequences, and the double-charge variant.

    One success after one or more failures is a healthy retry. Two or more
    successes after a failure means the retry logic settled twice.
    """
    patterns: list[DetectedPattern] = []
    groups: dict[tuple[str, str, str, str], list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.amount is None:
            continue
        groups[_amount_key(txn)].append(txn)

    for key, group in groups.items():
        ordered = _sorted_by_time(group)
        failures = [t for t in ordered if t.status is TransactionStatus.FAILED]
        if not failures:
            continue

        first_failure = failures[0]
        later_successes = [
            t
            for t in ordered
            if t.status is TransactionStatus.SUCCESS
            and (
                t.timestamp is None
                or first_failure.timestamp is None
                or (
                    t.timestamp >= first_failure.timestamp
                    and (t.timestamp - first_failure.timestamp).total_seconds()
                    <= config.retry_window_seconds
                )
            )
        ]
        if not later_successes:
            continue

        involved = failures + later_successes
        if len(later_successes) >= 2:
            patterns.append(
                DetectedPattern(
                    pattern_id="double_charge_after_retry",
                    name="Double charge after retry",
                    description=(
                        f"{len(failures)} failed attempt(s) of {key[2]} {key[3]} from "
                        f"{key[0]} to {key[1]} were followed by {len(later_successes)} "
                        f"successful settlements"
                    ),
                    severity=Severity.CRITICAL,
                    confidence=_clamp(0.72 + 0.06 * (len(later_successes) - 2)),
                    transaction_ids=[t.transaction_id for t in involved],
                    entities=_entities(involved),
                    metrics={
                        "failed_attempts": len(failures),
                        "successful_settlements": len(later_successes),
                        "amount": key[2],
                        "currency": key[3],
                        "retry_window_seconds": config.retry_window_seconds,
                    },
                    grade=EvidenceGrade.CONFIRMED_FACT,
                )
            )
        else:
            recovery = _seconds_between(first_failure, later_successes[0])
            patterns.append(
                DetectedPattern(
                    pattern_id="retry_after_failure",
                    name="Failed then successful retry",
                    description=(
                        f"{key[2]} {key[3]} from {key[0]} to {key[1]} failed "
                        f"{len(failures)} time(s) then settled once"
                        + (f" after {recovery:.0f}s" if recovery is not None else "")
                    ),
                    severity=Severity.LOW,
                    confidence=0.70,
                    transaction_ids=[t.transaction_id for t in involved],
                    entities=_entities(involved),
                    metrics={
                        "failed_attempts": len(failures),
                        "recovery_seconds": recovery,
                        "amount": key[2],
                        "currency": key[3],
                    },
                    grade=EvidenceGrade.CONFIRMED_FACT,
                )
            )
    return patterns


def detect_state_inconsistencies(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Reversals with no original, and settlements after a reversal."""
    patterns: list[DetectedPattern] = []
    ordered = _sorted_by_time(transactions)

    for txn in ordered:
        if txn.status not in (TransactionStatus.REVERSED, TransactionStatus.REFUNDED):
            continue
        key = _amount_key(txn)
        priors = [
            other
            for other in ordered
            if other is not txn
            and _amount_key(other) == key
            and other.status is TransactionStatus.SUCCESS
            and (
                other.timestamp is None
                or txn.timestamp is None
                or other.timestamp <= txn.timestamp
            )
        ]
        if priors:
            continue
        reference_match = [
            other
            for other in ordered
            if other is not txn
            and txn.reference_id
            and other.reference_id == txn.reference_id
            and other.status is TransactionStatus.SUCCESS
        ]
        if reference_match:
            continue
        patterns.append(
            DetectedPattern(
                pattern_id="orphan_reversal",
                name="Reversal without an original settlement",
                description=(
                    f"{txn.transaction_id} is a {txn.status.value} of {txn.amount} "
                    f"{txn.currency or ''} but no matching successful original appears "
                    f"in the evidence"
                ).replace("  ", " "),
                severity=Severity.MEDIUM,
                confidence=0.60,
                transaction_ids=[txn.transaction_id],
                entities=_entities([txn]),
                metrics={"status": txn.status.value, "amount": txn.amount},
                grade=EvidenceGrade.STRONG_EVIDENCE,
            )
        )

    # Settlement occurring after the same payment was already reversed.
    groups: dict[tuple[str, str, str, str], list[Transaction]] = defaultdict(list)
    for txn in ordered:
        if txn.amount is not None:
            groups[_amount_key(txn)].append(txn)
    for key, group in groups.items():
        stamped = [t for t in group if t.timestamp is not None]
        reversals = [
            t
            for t in stamped
            if t.status in (TransactionStatus.REVERSED, TransactionStatus.REFUNDED)
        ]
        if not reversals:
            continue
        earliest_reversal = min(t.timestamp for t in reversals)  # type: ignore[type-var]
        after = [
            t
            for t in stamped
            if t.status is TransactionStatus.SUCCESS and t.timestamp > earliest_reversal  # type: ignore[operator]
        ]
        if not after:
            continue
        involved = reversals + after
        patterns.append(
            DetectedPattern(
                pattern_id="settlement_after_reversal",
                name="Settlement after reversal",
                description=(
                    f"{key[2]} {key[3]} from {key[0]} to {key[1]} settled "
                    f"{len(after)} time(s) after an earlier reversal"
                ),
                severity=Severity.HIGH,
                confidence=0.70,
                transaction_ids=[t.transaction_id for t in involved],
                entities=_entities(involved),
                metrics={
                    "reversals": len(reversals),
                    "settlements_after_reversal": len(after),
                    "first_reversal_at": earliest_reversal,
                },
                grade=EvidenceGrade.CONFIRMED_FACT,
            )
        )
    return patterns


def detect_conflicting_records(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """One transaction_id appearing twice with different content.

    This is an evidence-integrity problem rather than a fraud signal, and it
    deliberately suppresses the overall confidence score downstream.
    """
    patterns: list[DetectedPattern] = []
    by_id: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if not txn.synthetic_id:
            by_id[txn.transaction_id].append(txn)

    for txn_id, group in sorted(by_id.items()):
        if len(group) < 2:
            continue
        differing: list[str] = []
        for field_name in ("amount", "currency", "status", "sender", "receiver", "timestamp"):
            values = {getattr(t, field_name) for t in group}
            if len(values) > 1:
                differing.append(field_name)
        if not differing:
            continue
        patterns.append(
            DetectedPattern(
                pattern_id="conflicting_records",
                name="Conflicting records for one transaction",
                description=(
                    f"{txn_id} appears {len(group)} times with disagreeing "
                    f"{', '.join(differing)} — the evidence contradicts itself"
                ),
                severity=Severity.HIGH,
                confidence=0.85,
                transaction_ids=[txn_id],
                entities=_entities(group),
                metrics={
                    "copies": len(group),
                    "conflicting_fields": differing,
                    "variants": [t.to_dict() for t in group],
                },
                grade=EvidenceGrade.CONFIRMED_FACT,
            )
        )
    return patterns


def detect_gateway_failure_clusters(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Failures concentrated on a single gateway or bank."""
    patterns: list[DetectedPattern] = []

    for label, table in (
        ("gateway", analysis.per_gateway_status),
        ("bank", analysis.per_bank_status),
    ):
        for name, counts in sorted(table.items()):
            total = sum(counts.values())
            failed = counts.get(TransactionStatus.FAILED.value, 0)
            if total < config.gateway_failure_min_count:
                continue
            rate = failed / total
            if rate < config.gateway_failure_min_rate:
                continue
            affected = [
                t
                for t in transactions
                if getattr(t, label) == name and t.status is TransactionStatus.FAILED
            ]
            patterns.append(
                DetectedPattern(
                    pattern_id="processor_failure_cluster",
                    name=f"Failure cluster on one {label}",
                    description=(
                        f"{failed} of {total} transactions routed through {label} "
                        f"{name!r} failed ({rate:.0%})"
                    ),
                    severity=Severity.MEDIUM,
                    confidence=_clamp(0.45 + 0.4 * rate),
                    transaction_ids=[t.transaction_id for t in affected],
                    entities=[name],
                    metrics={
                        "scope": label,
                        "name": name,
                        "failed": failed,
                        "total": total,
                        "failure_rate": round(rate, 3),
                        "case_failure_rate": round(analysis.failure_rate, 3),
                    },
                    grade=EvidenceGrade.CONFIRMED_FACT,
                )
            )
    return patterns


def detect_multi_currency_accounts(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """A single account transacting in several currencies."""
    patterns: list[DetectedPattern] = []
    for account, currencies in sorted(analysis.per_account_currencies.items()):
        if len(currencies) < 2:
            continue
        affected = [
            t
            for t in transactions
            if (t.account_id or t.sender) == account and t.currency
        ]
        patterns.append(
            DetectedPattern(
                pattern_id="multi_currency_account",
                name="Account operating in multiple currencies",
                description=(
                    f"{account} transacted in {len(currencies)} currencies "
                    f"({', '.join(currencies)})"
                ),
                severity=Severity.LOW,
                confidence=0.40,
                transaction_ids=[t.transaction_id for t in affected],
                entities=[account],
                metrics={"currencies": currencies},
                grade=EvidenceGrade.CONFIRMED_FACT,
            )
        )
    return patterns


def detect_self_transfers(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Transfers whose sender and receiver are the same entity."""
    affected = [
        t for t in transactions if t.sender and t.receiver and t.sender == t.receiver
    ]
    if not affected:
        return []
    return [
        DetectedPattern(
            pattern_id="self_transfer",
            name="Self-directed transfer",
            description=(
                f"{len(affected)} transaction(s) list the same entity as both sender "
                f"and receiver"
            ),
            severity=Severity.MEDIUM,
            confidence=0.65,
            transaction_ids=[t.transaction_id for t in affected],
            entities=_entities(affected),
            metrics={"count": len(affected)},
            grade=EvidenceGrade.CONFIRMED_FACT,
        )
    ]


# --------------------------------------------------------------------------- #
# Detectors: graph shape
# --------------------------------------------------------------------------- #
def _fan_pattern(
    graph: EntityGraph,
    config: InvestigationConfig,
    direction: str,
) -> list[DetectedPattern]:
    """Shared implementation for fan-in (many→one) and fan-out (one→many)."""
    patterns: list[DetectedPattern] = []
    inbound = direction == "in"

    hubs: dict[str, list[Edge]] = defaultdict(list)
    for edge in graph.edges:
        hubs[edge.target if inbound else edge.source].append(edge)

    for hub, all_edges in sorted(hubs.items()):
        stamped = sorted(
            (e for e in all_edges if e.timestamp is not None),
            key=lambda e: e.timestamp,  # type: ignore[arg-type,return-value]
        )

        if stamped:
            # Find the window of fan_window_seconds containing the most distinct
            # counterparties, so unrelated older activity by the same hub does
            # not mask a tight fan.
            best: list[Edge] = []
            best_parties: set[str] = set()
            left = 0
            for right in range(len(stamped)):
                while (
                    stamped[right].timestamp - stamped[left].timestamp  # type: ignore[operator]
                ).total_seconds() > config.fan_window_seconds:
                    left += 1
                window = stamped[left : right + 1]
                parties = {e.source if inbound else e.target for e in window}
                if len(parties) > len(best_parties):
                    best, best_parties = window, parties
            edges, counterparties = best, best_parties
            window_seconds: float | None = (
                (edges[-1].timestamp - edges[0].timestamp).total_seconds()  # type: ignore[operator]
                if edges
                else None
            )
        else:
            edges = list(all_edges)
            counterparties = {e.source if inbound else e.target for e in edges}
            window_seconds = None

        if len(counterparties) < config.fan_min_counterparties:
            continue

        total = sum((e.amount or Decimal("0")) for e in edges)
        patterns.append(
            DetectedPattern(
                pattern_id="fan_in" if inbound else "fan_out",
                name="Fan-in to one account" if inbound else "Fan-out from one account",
                description=(
                    f"{len(counterparties)} distinct "
                    f"{'senders paid' if inbound else 'receivers were paid by'} {hub}"
                    + (
                        f" within {window_seconds:.0f}s"
                        if window_seconds is not None
                        else ""
                    )
                    + f", totalling {total}"
                ),
                severity=Severity.HIGH,
                confidence=_clamp(
                    0.50 + 0.06 * (len(counterparties) - config.fan_min_counterparties)
                ),
                transaction_ids=[e.transaction_id for e in edges],
                entities=[hub, *sorted(counterparties)],
                metrics={
                    "hub": hub,
                    "counterparty_count": len(counterparties),
                    "window_seconds": window_seconds,
                    "total_amount": total,
                },
                grade=EvidenceGrade.STRONG_EVIDENCE,
            )
        )
    return patterns


def detect_fan_patterns(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Detect both fan-in and fan-out hub structures."""
    return _fan_pattern(graph, config, "in") + _fan_pattern(graph, config, "out")


def detect_transaction_chains(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Funds passing A→B→C quickly while retaining most of their value."""
    patterns: list[DetectedPattern] = []
    accepted: list[tuple[str, ...]] = []
    candidates: list[tuple[tuple[str, ...], list[Edge]]] = []

    for path in graph.find_paths(min_length=3, max_length=4, time_ordered=True):
        if any(edge.status != TransactionStatus.SUCCESS.value for edge in path):
            continue
        if any(edge.amount is None or edge.timestamp is None for edge in path):
            continue

        hops_ok = True
        for first, second in zip(path, path[1:]):
            elapsed = (second.timestamp - first.timestamp).total_seconds()  # type: ignore[operator]
            if elapsed > config.chain_window_seconds:
                hops_ok = False
                break
            if _relative_gap(first.amount, second.amount) > config.chain_amount_tolerance:  # type: ignore[arg-type]
                hops_ok = False
                break
        if not hops_ok:
            continue

        candidates.append((tuple([path[0].source, *(e.target for e in path)]), path))

    # Report only maximal chains: A→B→C→D subsumes A→B→C, and emitting both
    # would double-count the same flow as two findings.
    candidates.sort(key=lambda item: len(item[0]), reverse=True)
    for key, path in candidates:
        if any(_is_subpath(key, longer) for longer in accepted):
            continue
        accepted.append(key)

        elapsed_total = (path[-1].timestamp - path[0].timestamp).total_seconds()  # type: ignore[operator]
        patterns.append(
            DetectedPattern(
                pattern_id="pass_through_chain",
                name="Pass-through transaction chain",
                description=(
                    f"Funds moved {' → '.join(key)} in {elapsed_total:.0f}s with the "
                    f"amount essentially preserved at each hop"
                ),
                severity=Severity.HIGH,
                confidence=_clamp(0.55 + 0.08 * (len(path) - 2)),
                transaction_ids=[e.transaction_id for e in path],
                entities=list(key),
                metrics={
                    "hops": len(path),
                    "path": list(key),
                    "elapsed_seconds": elapsed_total,
                    "amounts": [e.amount for e in path],
                },
                grade=EvidenceGrade.STRONG_EVIDENCE,
            )
        )
    return patterns


def detect_circular_flows(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig,
) -> list[DetectedPattern]:
    """Money returning to its origin through one or more intermediaries."""
    patterns: list[DetectedPattern] = []
    for cycle in graph.find_cycles(max_length=config.max_cycle_length):
        members = set(cycle)
        involved = [
            e for e in graph.edges if e.source in members and e.target in members
        ]
        if not involved:
            continue
        patterns.append(
            DetectedPattern(
                pattern_id="circular_flow",
                name="Circular fund flow",
                description=(
                    f"Funds circulate {' → '.join(cycle)} → {cycle[0]}, returning to "
                    f"the originating account"
                ),
                severity=Severity.HIGH,
                confidence=_clamp(0.55 + 0.05 * len(cycle)),
                transaction_ids=[e.transaction_id for e in involved],
                entities=list(cycle),
                metrics={"cycle": cycle, "cycle_length": len(cycle)},
                grade=EvidenceGrade.CONFIRMED_FACT,
            )
        )
    return patterns


#: Detectors run in this order; the resulting list is sorted by severity.
DETECTORS: tuple[Detector, ...] = (
    detect_reference_collisions,
    detect_near_duplicates,
    detect_repeated_amounts,
    detect_bursts,
    detect_amount_anomalies,
    detect_structuring,
    detect_retry_sequences,
    detect_state_inconsistencies,
    detect_conflicting_records,
    detect_gateway_failure_clusters,
    detect_multi_currency_accounts,
    detect_self_transfers,
    detect_fan_patterns,
    detect_transaction_chains,
    detect_circular_flows,
    detect_off_hours_activity,
)


def detect_patterns(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    graph: EntityGraph,
    config: InvestigationConfig | None = None,
) -> list[DetectedPattern]:
    """Run every detector and return findings ordered by severity then confidence.

    A detector that raises is skipped rather than failing the whole
    investigation; partial analysis is more useful than none.
    """
    config = config or InvestigationConfig()
    findings: list[DetectedPattern] = []

    for detector in DETECTORS:
        try:
            findings.extend(detector(transactions, analysis, graph, config))
        except Exception as exc:  # pragma: no cover - defensive guard
            findings.append(
                DetectedPattern(
                    pattern_id="detector_error",
                    name="Detector failed",
                    description=(
                        f"Detector {detector.__name__} raised "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    severity=Severity.LOW,
                    confidence=0.0,
                    metrics={"detector": detector.__name__},
                    grade=EvidenceGrade.UNKNOWN,
                )
            )

    return sorted(
        findings, key=lambda p: (-severity_rank(p.severity), -p.confidence, p.pattern_id)
    )
