"""Stages 7 and 8 of the pipeline: evidence linking and confidence scoring.

Two rules govern this module:

1. **Every conclusion cites its evidence.** Findings are emitted as
   :class:`~agent.models.EvidenceItem` objects that name the transaction id
   they came from.
2. **Confidence is calculated, not asserted.** :func:`score_confidence`
   implements one documented formula so two runs over the same evidence always
   produce the same number, and so a reviewer can see *why* the agent is
   unsure.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .analyzer import TransactionAnalysis
from .config import InvestigationConfig
from .models import (
    DataQuality,
    DetectedPattern,
    EvidenceGrade,
    EvidenceItem,
    Importance,
    Provenance,
    Severity,
    Transaction,
    importance_for_severity,
    max_severity,
    severity_rank,
)

__all__ = [
    "ConfidenceBreakdown",
    "score_confidence",
    "build_evidence",
    "select_suspicious_transactions",
    "collect_confirmed_facts",
    "collect_unknowns",
    "overall_severity",
]

#: How much a pattern's severity contributes to a transaction's risk score.
_SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.LOW: 0.35,
    Severity.MEDIUM: 0.60,
    Severity.HIGH: 0.85,
    Severity.CRITICAL: 1.00,
}

#: Volume at which the case is considered to have a full statistical sample.
_FULL_SAMPLE = 10.0


class ConfidenceBreakdown(dict):
    """A plain dict subclass documenting the confidence inputs in the report."""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def data_quality_score(quality: DataQuality | None) -> float:
    """Score how usable the submitted evidence is, in ``[0, 1]``.

    ``0.45 * field_completeness + 0.25 * volume + 0.30 * integrity`` where
    *volume* saturates at ten parsed records and *integrity* is reduced by
    rejected and self-contradicting records.
    """
    if quality is None or quality.records_parsed == 0:
        return 0.0
    volume = min(1.0, quality.records_parsed / _FULL_SAMPLE)
    conflict_ratio = quality.conflicting_record_count / max(1, quality.records_parsed)
    integrity = max(0.0, 1.0 - conflict_ratio - quality.rejection_ratio)
    return _clamp(
        0.45 * quality.field_completeness + 0.25 * volume + 0.30 * integrity, 0.0, 1.0
    )


def score_confidence(
    supporting: Sequence[DetectedPattern],
    quality: DataQuality | None,
) -> tuple[float, ConfidenceBreakdown]:
    """Return ``(confidence, breakdown)`` for a conclusion.

    With supporting patterns::

        0.55 * strongest_pattern_confidence
      + 0.30 * data_quality
      + 0.15 * corroboration          (extra independent patterns, saturating at 4)

    With no supporting patterns the conclusion is "nothing suspicious found",
    whose credibility depends only on how good the data was::

        0.15 + 0.75 * data_quality

    Both results are clamped away from 0.0 and 1.0 — the agent never claims
    certainty.
    """
    quality_score = data_quality_score(quality)

    if not supporting:
        confidence = _clamp(0.15 + 0.75 * quality_score, 0.10, 0.85)
        breakdown = ConfidenceBreakdown(
            mode="absence_of_findings",
            data_quality=round(quality_score, 3),
            pattern_strength=0.0,
            corroboration=0.0,
            formula="0.15 + 0.75 * data_quality",
        )
        return round(confidence, 3), breakdown

    strength = max(p.confidence for p in supporting)
    distinct = len({p.pattern_id for p in supporting})
    corroboration = min(1.0, (distinct - 1) / 3.0)
    confidence = _clamp(
        0.55 * strength + 0.30 * quality_score + 0.15 * corroboration, 0.05, 0.95
    )
    breakdown = ConfidenceBreakdown(
        mode="supported_by_patterns",
        data_quality=round(quality_score, 3),
        pattern_strength=round(strength, 3),
        corroboration=round(corroboration, 3),
        distinct_patterns=distinct,
        formula="0.55 * pattern_strength + 0.30 * data_quality + 0.15 * corroboration",
    )
    return round(confidence, 3), breakdown


# --------------------------------------------------------------------------- #
# Evidence linking
# --------------------------------------------------------------------------- #
def build_evidence(
    transactions: Sequence[Transaction],
    patterns: Sequence[DetectedPattern],
    quality: DataQuality | None = None,
    config: InvestigationConfig | None = None,
) -> list[EvidenceItem]:
    """Turn detected patterns and data-quality issues into cited findings."""
    config = config or InvestigationConfig()
    by_id = {t.transaction_id: t for t in transactions}
    items: list[EvidenceItem] = []
    seen: set[tuple[str | None, str]] = set()

    for pattern in patterns:
        if pattern.pattern_id == "detector_error":
            continue
        importance = importance_for_severity(pattern.severity)
        targets: Iterable[str | None] = pattern.transaction_ids or [None]
        for txn_id in targets:
            txn = by_id.get(txn_id) if txn_id else None
            detail = pattern.description
            if txn is not None and txn.amount is not None:
                detail = (
                    f"{pattern.name}: {pattern.description} "
                    f"(this record: {txn.amount} {txn.currency or ''}, "
                    f"{txn.status.value})"
                ).replace("  ", " ").strip()
            else:
                detail = f"{pattern.name}: {pattern.description}"

            key = (txn_id, detail)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                EvidenceItem(
                    transaction_id=txn_id,
                    finding=detail,
                    importance=importance,
                    pattern_id=pattern.pattern_id,
                    grade=pattern.grade,
                    provenance=Provenance.DETERMINISTIC,
                )
            )

    # Data-quality problems are evidence too: they explain low confidence.
    if quality is not None:
        for issue in quality.issues:
            if issue.severity is Severity.LOW:
                continue
            key = (issue.transaction_id, issue.problem)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                EvidenceItem(
                    transaction_id=issue.transaction_id,
                    finding=f"Evidence gap in field '{issue.field}': {issue.problem}",
                    importance=(
                        Importance.MEDIUM
                        if issue.severity is Severity.HIGH
                        else Importance.LOW
                    ),
                    pattern_id=None,
                    grade=EvidenceGrade.UNKNOWN,
                    provenance=Provenance.DETERMINISTIC,
                )
            )

    order = {Importance.HIGH: 0, Importance.MEDIUM: 1, Importance.LOW: 2}
    items.sort(key=lambda i: (order[i.importance], i.pattern_id or "zz", i.transaction_id or ""))
    return items[: config.max_evidence_items]


def risk_score(patterns: Sequence[DetectedPattern]) -> float:
    """Risk for one transaction: weighted strongest signal plus corroboration."""
    if not patterns:
        return 0.0
    weighted = max(_SEVERITY_WEIGHT[p.severity] * p.confidence for p in patterns)
    bonus = 0.05 * (len({p.pattern_id for p in patterns}) - 1)
    return round(_clamp(weighted + bonus, 0.0, 1.0), 3)


def select_suspicious_transactions(
    transactions: Sequence[Transaction],
    patterns: Sequence[DetectedPattern],
) -> list[dict[str, Any]]:
    """Return the implicated transactions, most suspicious first."""
    by_id = {t.transaction_id: t for t in transactions}
    grouped: dict[str, list[DetectedPattern]] = {}
    for pattern in patterns:
        if pattern.pattern_id == "detector_error":
            continue
        for txn_id in pattern.transaction_ids:
            grouped.setdefault(txn_id, []).append(pattern)

    rows: list[dict[str, Any]] = []
    for txn_id, involved in grouped.items():
        txn = by_id.get(txn_id)
        severity = max_severity([p.severity for p in involved])
        rows.append(
            {
                "transaction_id": txn_id,
                "timestamp": txn.timestamp if txn else None,
                "amount": txn.amount if txn else None,
                "currency": txn.currency if txn else None,
                "status": txn.status.value if txn else None,
                "sender": txn.sender if txn else None,
                "receiver": txn.receiver if txn else None,
                "gateway": txn.gateway if txn else None,
                "severity": severity.value,
                "risk_score": risk_score(involved),
                "reasons": sorted({p.name for p in involved}),
                "pattern_ids": sorted({p.pattern_id for p in involved}),
            }
        )

    rows.sort(
        key=lambda r: (
            -severity_rank(Severity(r["severity"])),
            -float(r["risk_score"]),
            str(r["transaction_id"]),
        )
    )
    return rows


# --------------------------------------------------------------------------- #
# Facts and unknowns
# --------------------------------------------------------------------------- #
def collect_confirmed_facts(
    analysis: TransactionAnalysis,
    patterns: Sequence[DetectedPattern],
    quality: DataQuality | None = None,
) -> list[str]:
    """Statements that are true by direct calculation over the evidence."""
    facts: list[str] = []
    amounts = analysis.amounts

    facts.append(
        f"{analysis.transaction_count} transaction record(s) were parsed from the "
        f"submitted evidence."
    )
    if amounts.count:
        currencies = ", ".join(sorted(amounts.currencies)) or "unspecified currency"
        facts.append(
            f"The {amounts.count} record(s) carrying an amount total {amounts.total} "
            f"({currencies}), ranging {amounts.minimum} to {amounts.maximum} with a "
            f"median of {amounts.median:.2f}."
            if amounts.median is not None
            else f"The records total {amounts.total}."
        )
    if analysis.status_counts:
        breakdown = ", ".join(
            f"{count} {status}" for status, count in sorted(analysis.status_counts.items())
        )
        facts.append(f"Status breakdown: {breakdown}.")
    if analysis.timing.first_event and analysis.timing.last_event:
        facts.append(
            f"Activity spans {analysis.timing.first_event.isoformat()} to "
            f"{analysis.timing.last_event.isoformat()} "
            f"({analysis.timing.span_seconds:.0f} seconds)."
        )
    if quality is not None and quality.conflicting_record_count:
        facts.append(
            f"{quality.conflicting_record_count} record(s) contradict another record "
            f"sharing the same transaction_id."
        )

    for pattern in patterns:
        if pattern.grade is EvidenceGrade.CONFIRMED_FACT:
            citation = (
                f" [{', '.join(pattern.transaction_ids[:5])}]"
                if pattern.transaction_ids
                else ""
            )
            facts.append(f"{pattern.description}.{citation}")

    return facts


def collect_unknowns(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    quality: DataQuality | None,
    patterns: Sequence[DetectedPattern],
) -> list[str]:
    """Everything the evidence does not tell us, stated explicitly."""
    unknowns: list[str] = []

    if quality is not None:
        if quality.records_parsed == 0:
            unknowns.append(
                "No usable transaction records were provided, so nothing about this "
                "case can be established."
            )
        if quality.records_rejected:
            unknowns.append(
                f"{quality.records_rejected} submitted record(s) could not be parsed "
                f"and were excluded from analysis."
            )
        for field_name, count in sorted(quality.missing_fields.items()):
            unknowns.append(
                f"{count} record(s) are missing '{field_name}', limiting analysis that "
                f"depends on it."
            )
        if quality.conflicting_record_count:
            unknowns.append(
                "Which variant of the contradictory records is authoritative is "
                "unknown; the source system of record has not been identified."
            )

    if analysis.transaction_count and analysis.transaction_count < 3:
        unknowns.append(
            "The sample is too small to establish a behavioural baseline, so "
            "statistical outlier detection was not applied."
        )
    if analysis.amounts.count and analysis.amounts.count < 5:
        unknowns.append(
            "Fewer than five amounts were available, so amount-distribution outlier "
            "scoring was skipped."
        )
    if analysis.timing.missing_timestamps:
        unknowns.append(
            f"{analysis.timing.missing_timestamps} record(s) lack a timestamp and could "
            f"not be placed on the timeline."
        )
    if not any(t.reference_id for t in transactions):
        unknowns.append(
            "No reference/idempotency keys were supplied, so duplicate settlements can "
            "only be inferred from party, amount and timing."
        )
    if not any(t.gateway for t in transactions) and not any(t.bank for t in transactions):
        unknowns.append(
            "No gateway or bank was recorded, so the processing path cannot be "
            "attributed to a specific provider."
        )
    if any(p.pattern_id == "detector_error" for p in patterns):
        unknowns.append(
            "One or more detectors failed to run; the pattern list may be incomplete."
        )

    return unknowns


def overall_severity(
    patterns: Sequence[DetectedPattern],
    analysis: TransactionAnalysis,
) -> Severity:
    """Case severity: the worst pattern, escalated by breadth of impact."""
    if not patterns:
        return Severity.LOW

    base = max_severity([p.severity for p in patterns])
    implicated = {
        txn_id
        for p in patterns
        if p.severity in (Severity.HIGH, Severity.CRITICAL)
        for txn_id in p.transaction_ids
    }
    high_confidence_serious = [
        p
        for p in patterns
        if p.severity in (Severity.HIGH, Severity.CRITICAL) and p.confidence >= 0.7
    ]

    # Several independent serious findings escalate a HIGH case to CRITICAL.
    if (
        base is Severity.HIGH
        and len({p.pattern_id for p in high_confidence_serious}) >= 3
        and analysis.transaction_count
        and len(implicated) / analysis.transaction_count >= 0.5
    ):
        return Severity.CRITICAL
    return base
