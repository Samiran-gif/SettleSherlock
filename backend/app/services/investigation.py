"""Deterministic settlement investigation engine.

Takes the three system records for one transaction - exactly the dict that
``DataLoader.find_transaction`` returns - and derives a settlement status, a
root cause, the supporting evidence, any exceptions and a recommended action.

Two properties are deliberate:
  * Reproducible. The same records always yield the same result. No randomness,
    no transaction id is special-cased, no AI involved.
  * Honest. Every evidence line is read off an actual record. When the records
    do not support a conclusion, the root cause says so instead of guessing.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from app.schemas.investigation import InvestigationResponse, SettlementStatus
from app.schemas.transaction import TransactionRecord

# --- Status vocabulary -------------------------------------------------------
# The mock CSVs use a small set of status words per system. Grouping them by
# meaning keeps the rules readable and makes unknown values easy to spot.

SOURCE_ORDER = ("gateway", "bank", "ledger")
SOURCE_LABELS = {"gateway": "Gateway", "bank": "Bank", "ledger": "Ledger"}

# Statuses meaning "this system considers the money moved".
SUCCESS_STATUSES = {
    "gateway": {"SUCCESS", "SUCCESSFUL", "CAPTURED", "COMPLETED"},
    "bank": {"SETTLED", "SUCCESS", "CREDITED", "COMPLETED"},
    "ledger": {"RECORDED", "POSTED", "SUCCESS", "SETTLED"},
}

# Statuses meaning "this system considers the money did not move".
FAILURE_STATUSES = {
    "gateway": {"FAILED", "FAILURE", "DECLINED"},
    "bank": {"FAILED", "RETURNED", "REJECTED", "REVERSED"},
    "ledger": {"REVERSED", "ROLLED_BACK", "FAILED", "NOT_RECORDED"},
}

# Statuses meaning "this system has not finished yet".
PENDING_STATUSES = {
    "gateway": set(),
    "bank": {"DELAYED", "PENDING", "PROCESSING", "IN_PROGRESS"},
    "ledger": set(),
}

# --- Thresholds --------------------------------------------------------------

# How much later than the gateway the bank leg may be before we call it a delay.
BANK_DELAY_THRESHOLD = timedelta(hours=2)

# Amounts are floats in the CSVs, so compare with a tolerance rather than ==.
AMOUNT_TOLERANCE = 0.01

# --- Fixed wording -----------------------------------------------------------

RECOMMENDED_ACTIONS = {
    SettlementStatus.SETTLED: "Confirm settlement reference with the support request.",
    SettlementStatus.DELAYED: "Verify bank settlement batch and bank reference.",
    SettlementStatus.FAILED: (
        "Review gateway failure reason and retry/reconciliation status."
    ),
    SettlementStatus.INCOMPLETE: (
        "Check the missing system record and reconciliation logs."
    ),
    SettlementStatus.NEEDS_INVESTIGATION: (
        "Review the inconsistent records and reconcile the affected systems."
    ),
}

UNCONFIRMED_ROOT_CAUSE = "Root cause cannot be confirmed from available evidence."
SETTLED_ROOT_CAUSE = "Settlement is consistent across gateway, bank and ledger"


@dataclass
class Finding:
    """One problem detected in the records.

    ``status`` and ``root_cause`` are what this finding alone would conclude.
    ``summary`` is the short phrase used when several findings are combined.
    ``always_exception`` marks findings that are reported as exceptions even
    when they are the headline conclusion (missing records, mismatched amounts).
    """

    code: str
    status: SettlementStatus
    root_cause: str
    summary: str
    confidence_penalty: int
    exceptions: List[str] = field(default_factory=list)
    always_exception: bool = False


# --- Small helpers -----------------------------------------------------------


def _join(parts: List[str]) -> str:
    """Join names for prose: one name, two joined by "and", or a list."""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _present(
    records: Dict[str, Optional[TransactionRecord]]
) -> List[Tuple[str, TransactionRecord]]:
    """The (source, record) pairs that exist, in gateway/bank/ledger order."""
    return [
        (source, records[source]) for source in SOURCE_ORDER if records.get(source)
    ]


def _outcome(source: str, record: TransactionRecord) -> str:
    """Classify one record as success / failure / pending / unknown."""
    status = record.status.upper()
    if status in SUCCESS_STATUSES[source]:
        return "success"
    if status in FAILURE_STATUSES[source]:
        return "failure"
    if status in PENDING_STATUSES[source]:
        return "pending"
    return "unknown"


def _mismatched_amount_pairs(
    records: Dict[str, Optional[TransactionRecord]]
) -> List[str]:
    """Describe every pair of present records whose amounts disagree."""
    present = _present(records)
    pairs = []
    for index, (source, record) in enumerate(present):
        for other_source, other_record in present[index + 1:]:
            if abs(record.amount - other_record.amount) > AMOUNT_TOLERANCE:
                pairs.append(
                    f"{source} ({record.amount:.2f}) "
                    f"and {other_source} ({other_record.amount:.2f})"
                )
    return pairs


def _bank_delay_gap(
    records: Dict[str, Optional[TransactionRecord]]
) -> Optional[timedelta]:
    """How far the bank timestamp trails the gateway, if beyond the threshold."""
    gateway = records.get("gateway")
    bank = records.get("bank")
    if gateway is None or bank is None:
        return None

    try:
        gap = bank.timestamp - gateway.timestamp
    except TypeError:  # one timestamp tz-aware, the other naive
        return None

    return gap if gap > BANK_DELAY_THRESHOLD else None


def _format_gap(gap: timedelta) -> str:
    """Render a timedelta as "6h 13m" for evidence lines."""
    total_minutes = int(gap.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


# --- Rules -------------------------------------------------------------------
# Each detector inspects the records and returns a Finding, or None if that
# particular problem is not present.


def _detect_missing_records(records) -> Optional[Finding]:
    """RULE 4 - the transaction is absent from at least one system."""
    missing = [source for source in SOURCE_ORDER if records.get(source) is None]
    if not missing:
        return None

    labels = [SOURCE_LABELS[source] for source in missing]
    noun = "record" if len(missing) == 1 else "records"
    root_cause = f"{_join(labels)} {noun} missing"

    return Finding(
        code="MISSING_RECORD",
        status=SettlementStatus.INCOMPLETE,
        root_cause=root_cause,
        summary=root_cause.lower(),
        # Each absent system is a real hole in the evidence.
        confidence_penalty=30 * len(missing),
        exceptions=[
            f"{SOURCE_LABELS[source]} record was not found" for source in missing
        ],
        always_exception=True,
    )


def _detect_gateway_failure(records) -> Optional[Finding]:
    """RULE 2 - the gateway itself reports a failed transaction."""
    gateway = records.get("gateway")
    if gateway is None or _outcome("gateway", gateway) != "failure":
        return None

    return Finding(
        code="GATEWAY_FAILED",
        status=SettlementStatus.FAILED,
        root_cause="Gateway transaction failed",
        summary="gateway transaction failed",
        # A clear, self-consistent failure: the evidence is not in doubt.
        confidence_penalty=0,
        exceptions=[f"Gateway reported status {gateway.status}"],
    )


def _detect_status_contradiction(records) -> Optional[Finding]:
    """Systems that have finished disagree on whether the money moved.

    Backs RULE 1: a clean SETTLED requires every finished system to agree.
    """
    decided = [
        (source, record)
        for source, record in _present(records)
        if _outcome(source, record) in {"success", "failure"}
    ]
    outcomes = {_outcome(source, record) for source, record in decided}
    if len(outcomes) < 2:
        return None

    detail = ", ".join(
        f"{source} status {record.status}" for source, record in decided
    )
    return Finding(
        code="STATUS_CONTRADICTION",
        status=SettlementStatus.NEEDS_INVESTIGATION,
        root_cause="Conflicting settlement outcomes between systems",
        summary="conflicting settlement outcomes between systems",
        confidence_penalty=15,
        exceptions=[f"Systems disagree on the outcome: {detail}"],
        always_exception=True,
    )


def _detect_amount_mismatch(records) -> Optional[Finding]:
    """RULE 5 - available systems hold different amounts."""
    pairs = _mismatched_amount_pairs(records)
    if not pairs:
        return None

    return Finding(
        code="AMOUNT_MISMATCH",
        status=SettlementStatus.NEEDS_INVESTIGATION,
        root_cause="Amount mismatch between systems",
        summary="amount mismatch between systems",
        confidence_penalty=15,
        exceptions=["Amount mismatch between " + "; ".join(pairs)],
        always_exception=True,
    )


def _detect_bank_delay(records) -> Optional[Finding]:
    """RULE 3 - the bank leg is pending, or lands much later than the gateway."""
    bank = records.get("bank")
    if bank is None:
        return None

    # A failed bank leg is a failure, not a delay.
    if _outcome("bank", bank) == "failure":
        return None

    pending = _outcome("bank", bank) == "pending"
    gap = _bank_delay_gap(records)
    if not pending and gap is None:
        return None

    return Finding(
        code="BANK_DELAY",
        status=SettlementStatus.DELAYED,
        root_cause="Bank-side settlement delay",
        summary="bank-side settlement delay",
        # The story is clear, only the bank timing is outstanding.
        confidence_penalty=6,
        exceptions=[
            "Bank settlement is delayed relative to the gateway authorisation"
        ],
    )


def _detect_unrecognised_statuses(records) -> Optional[Finding]:
    """A status value we cannot interpret. Report it rather than assume."""
    unknown = [
        (source, record)
        for source, record in _present(records)
        if _outcome(source, record) == "unknown"
    ]
    if not unknown:
        return None

    return Finding(
        code="UNRECOGNISED_STATUS",
        status=SettlementStatus.NEEDS_INVESTIGATION,
        root_cause=UNCONFIRMED_ROOT_CAUSE,
        summary="unrecognised status values",
        confidence_penalty=12,
        exceptions=[
            f"{SOURCE_LABELS[source]} status "
            f"{record.status} is not recognised"
            for source, record in unknown
        ],
        always_exception=True,
    )


# Evaluated in this order; the order only affects how combined wording reads.
DETECTORS = (
    _detect_missing_records,
    _detect_gateway_failure,
    _detect_status_contradiction,
    _detect_amount_mismatch,
    _detect_bank_delay,
    _detect_unrecognised_statuses,
)


# --- Evidence ----------------------------------------------------------------


def _build_evidence(
    transaction_id: str,
    records: Dict[str, Optional[TransactionRecord]],
    findings: List[Finding],
) -> List[str]:
    """State the facts the conclusion rests on, all read off the records."""
    evidence = []

    for source in SOURCE_ORDER:
        record = records.get(source)
        if record is not None:
            evidence.append(f"{SOURCE_LABELS[source]} status is {record.status}")
        else:
            evidence.append(f"No {source} record was found for {transaction_id}")

    present = _present(records)
    if len(present) > 1:
        mismatched = _mismatched_amount_pairs(records)
        if mismatched:
            evidence.append("Amounts differ between " + "; ".join(mismatched))
        else:
            labels = _join([SOURCE_LABELS[source] for source, _ in present])
            evidence.append(
                f"{labels} amounts match at {present[0][1].amount:.2f}"
            )

    if any(finding.code == "BANK_DELAY" for finding in findings):
        gap = _bank_delay_gap(records)
        if gap is not None:
            bank = records["bank"]
            gateway = records["gateway"]
            evidence.append(
                f"Bank timestamp {bank.timestamp.isoformat()} is "
                f"{_format_gap(gap)} later than the gateway timestamp "
                f"{gateway.timestamp.isoformat()}"
            )

    return evidence


# --- Confidence --------------------------------------------------------------


def _confidence(findings: List[Finding]) -> int:
    """Score 0-100 for how complete and consistent the evidence is.

    Starts from a full 100 and subtracts a fixed penalty per finding, so the
    same records always produce the same number. This measures evidence
    quality only - a cleanly failed transaction still scores 100, because all
    three systems agree on what happened.
    """
    score = 100 - sum(finding.confidence_penalty for finding in findings)
    return max(0, min(100, score))


# --- Entry point -------------------------------------------------------------


def investigate(
    transaction_id: str,
    records: Dict[str, Optional[TransactionRecord]],
) -> InvestigationResponse:
    """Investigate one transaction from its gateway, bank and ledger records."""
    records = {source: records.get(source) for source in SOURCE_ORDER}

    # Nothing to reason about. Say so rather than inventing a cause.
    if not any(records.values()):
        return InvestigationResponse(
            transaction_id=transaction_id,
            status=SettlementStatus.INCOMPLETE,
            root_cause=UNCONFIRMED_ROOT_CAUSE,
            investigation_confidence=0,
            evidence=[
                f"No gateway, bank or ledger record was found for {transaction_id}"
            ],
            exceptions=["No record of this transaction was found in any system"],
            recommended_action=RECOMMENDED_ACTIONS[SettlementStatus.INCOMPLETE],
        )

    findings = [
        finding
        for finding in (detector(records) for detector in DETECTORS)
        if finding is not None
    ]

    if not findings:
        # RULE 1 - every system present, agreeing, with matching amounts.
        status = SettlementStatus.SETTLED
        root_cause = SETTLED_ROOT_CAUSE
    elif len(findings) == 1:
        status = findings[0].status
        root_cause = findings[0].root_cause
    else:
        # RULE 6 - never single out one problem when several exist.
        status = SettlementStatus.NEEDS_INVESTIGATION
        root_cause = "Multiple inconsistencies detected: " + "; ".join(
            finding.summary for finding in findings
        )

    # A lone finding is already stated by the status, so it only becomes an
    # exception if it is one we always report. Once several problems exist,
    # none of them is captured by the status, so all of them are reported.
    headline = findings[0] if len(findings) == 1 else None
    exceptions = []
    for finding in findings:
        if finding.always_exception or finding is not headline:
            exceptions.extend(finding.exceptions)

    return InvestigationResponse(
        transaction_id=transaction_id,
        status=status,
        root_cause=root_cause,
        investigation_confidence=_confidence(findings),
        evidence=_build_evidence(transaction_id, records, findings),
        exceptions=exceptions,
        recommended_action=RECOMMENDED_ACTIONS[status],
    )
