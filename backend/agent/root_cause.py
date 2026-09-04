"""Stage 6 and 9 of the pipeline: root-cause reasoning and recommendations.

The reasoning here is an explicit rule base rather than a language model. Each
rule states which detected patterns imply a candidate explanation, which
patterns would weaken it, and what evidence would settle the question. That
makes the agent's conclusions auditable: a reviewer can point at the rule that
fired and at the transactions that triggered it.

A language model may later *rephrase* these conclusions (see :mod:`agent.llm`),
but it never produces them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .analyzer import TransactionAnalysis
from .config import InvestigationConfig
from .evidence import score_confidence
from .models import (
    DataQuality,
    DetectedPattern,
    EvidenceGrade,
    Hypothesis,
    Provenance,
    Severity,
    Transaction,
    max_severity,
)

__all__ = ["RootCauseRule", "RULES", "analyze_root_cause", "RootCauseResult"]


@dataclass(slots=True, frozen=True)
class RootCauseRule:
    """A single auditable inference from patterns to a candidate root cause."""

    rule_id: str
    conclusion: str
    #: Pattern ids that trigger the rule.
    triggers: tuple[str, ...]
    #: ``"any"`` fires on one trigger; ``"all"`` requires every trigger.
    mode: str = "any"
    #: Prior plausibility of this explanation; scales the final confidence.
    weight: float = 1.0
    #: Static rationale appended after the observed-pattern lines.
    rationale: tuple[str, ...] = ()
    #: Patterns whose presence argues against this explanation.
    weakened_by: tuple[str, ...] = ()
    #: Evidence requests that would confirm or reject this explanation.
    next_steps: tuple[str, ...] = ()

    def matches(self, present: set[str]) -> bool:
        if self.mode == "all":
            return all(t in present for t in self.triggers)
        return any(t in present for t in self.triggers)


#: The rule base, most specific / highest-signal first.
RULES: tuple[RootCauseRule, ...] = (
    RootCauseRule(
        rule_id="duplicate_settlement",
        conclusion=(
            "The same payment was settled more than once. The evidence is most "
            "consistent with a duplicate-charge defect in the payment submission or "
            "retry path rather than two genuine customer payments."
        ),
        triggers=("duplicate_settlement_reference", "double_charge_after_retry"),
        weight=1.0,
        rationale=(
            "Two settlements sharing one reference/idempotency key cannot both be "
            "authorised instructions; the reference is by definition unique per "
            "payment intent.",
            "The duplicated leg should be refundable without customer dispute.",
        ),
        weakened_by=("settlement_after_reversal",),
        next_steps=(
            "Retrieve the gateway request log for the shared reference id, including "
            "client retry counts and idempotency-key handling.",
            "Confirm with the ledger whether both settlements were posted to the "
            "customer account or only one.",
            "Check whether a refund or reversal has already been issued for the "
            "duplicated leg.",
        ),
    ),
    RootCauseRule(
        rule_id="retry_without_idempotency",
        conclusion=(
            "Payment retries are being issued without an effective idempotency guard, "
            "so a failed attempt that actually succeeded downstream was charged again."
        ),
        triggers=("retry_after_failure", "near_duplicate_transaction"),
        mode="all",
        weight=0.92,
        rationale=(
            "A failure immediately followed by an identical successful transfer is the "
            "signature of a client-side retry.",
            "Because the retry produced a second value-moving record, the failure was "
            "reported to the client but not to the ledger.",
        ),
        next_steps=(
            "Obtain the gateway response codes for the failed attempts to determine "
            "whether the failure was a genuine decline or a timeout.",
            "Verify whether the retry reused the original idempotency key.",
        ),
    ),
    RootCauseRule(
        rule_id="duplicate_submission",
        conclusion=(
            "An identical payment instruction was submitted more than once in quick "
            "succession — consistent with a double submission (client double-click, "
            "webhook replay or batch re-run) rather than intentional misuse."
        ),
        triggers=("near_duplicate_transaction",),
        weight=0.80,
        rationale=(
            "The transfers share sender, receiver, amount and currency and fall inside "
            "the duplicate-detection window, which is far tighter than normal "
            "customer behaviour.",
        ),
        weakened_by=("repeated_identical_amount",),
        next_steps=(
            "Pull the request ids / client fingerprints for the duplicate cluster to "
            "distinguish a UI double-submit from a server-side replay.",
            "Confirm whether the receiving ledger recorded both legs.",
        ),
    ),
    RootCauseRule(
        rule_id="processor_fault",
        conclusion=(
            "A provider-side or technical fault concentrated on one processing route "
            "explains the failures; this looks like an integration/availability "
            "problem, not suspicious customer activity."
        ),
        triggers=("processor_failure_cluster",),
        weight=0.88,
        rationale=(
            "Failures cluster on a single gateway or bank while other routes in the "
            "same case behave normally, which points at the route rather than at the "
            "payers.",
        ),
        next_steps=(
            "Correlate the failure window with the provider's status page and internal "
            "error-rate dashboards.",
            "Collect the raw decline/error codes for the failed transactions.",
        ),
    ),
    RootCauseRule(
        rule_id="reconciliation_defect",
        conclusion=(
            "The records describe a transaction lifecycle that cannot physically have "
            "happened, indicating a state-synchronisation or reconciliation defect "
            "between the systems that produced this evidence."
        ),
        triggers=(
            "shared_reference_mixed_state",
            "settlement_after_reversal",
            "conflicting_records",
            "orphan_reversal",
        ),
        weight=0.85,
        rationale=(
            "Conflicting or impossible state transitions are a property of the "
            "recording systems, so the defect is in the data pipeline or in the "
            "ordering of state updates.",
            "Until the system of record is identified, downstream fraud conclusions "
            "drawn from these records are unsafe.",
        ),
        next_steps=(
            "Identify the authoritative system of record and re-export the affected "
            "transactions from it.",
            "Retrieve the full status-transition audit log (with write timestamps) for "
            "the affected transaction ids.",
        ),
    ),
    RootCauseRule(
        rule_id="account_compromise",
        conclusion=(
            "The sending account behaved as if it were under third-party control: a "
            "sudden burst of outbound value inconsistent with its own history."
        ),
        triggers=("transaction_burst",),
        mode="all",
        weight=0.82,
        rationale=(
            "Rapid sequential outbound transfers are the standard draining pattern "
            "after credential or session compromise.",
        ),
        weakened_by=("repeated_identical_amount",),
        next_steps=(
            "Pull authentication and device/IP history for the sending account "
            "covering the burst window.",
            "Check for a preceding profile change (password, payee, contact details) "
            "or a step-up authentication bypass.",
            "Contact the account holder to confirm whether the transfers were "
            "authorised.",
        ),
    ),
    RootCauseRule(
        rule_id="automated_batch",
        conclusion=(
            "The activity looks machine-generated but internally consistent — most "
            "likely an automated batch, scheduled disbursement or scripted test run "
            "rather than an attack."
        ),
        triggers=("repeated_identical_amount", "transaction_burst"),
        mode="all",
        weight=0.60,
        rationale=(
            "Identical amounts on a regular cadence are characteristic of scheduled "
            "processing; a burst alone does not imply intent.",
        ),
        next_steps=(
            "Confirm with the owning team whether a batch job or migration ran in the "
            "burst window.",
            "Compare the burst against the account's historical baseline for the same "
            "weekday and hour.",
        ),
    ),
    RootCauseRule(
        rule_id="layering",
        conclusion=(
            "Funds were moved through intermediary accounts in a way that preserves "
            "value while obscuring origin — the layering stage of a laundering "
            "attempt."
        ),
        triggers=("pass_through_chain", "circular_flow"),
        weight=0.90,
        rationale=(
            "Each hop retains nearly the full amount and occurs within a short window, "
            "so the intermediaries are conduits rather than economic counterparties.",
        ),
        next_steps=(
            "Run KYC / beneficial-ownership checks on every intermediary account in "
            "the chain.",
            "Extend the transaction pull to 30 days either side to establish whether "
            "the chain is habitual.",
            "Check whether the intermediary accounts were opened recently or share "
            "contact details.",
        ),
    ),
    RootCauseRule(
        rule_id="structuring",
        conclusion=(
            "Transfers were deliberately sized just below a reporting threshold, "
            "indicating structuring to avoid disclosure."
        ),
        triggers=("threshold_structuring",),
        weight=0.88,
        rationale=(
            "The amounts cluster in a narrow band immediately under a round reporting "
            "threshold while their total substantially exceeds it, which is unlikely "
            "to arise from genuine pricing.",
        ),
        next_steps=(
            "File / review the threshold-reporting obligation for the aggregate amount.",
            "Review the sender's history for the same banding over a longer period.",
        ),
    ),
    RootCauseRule(
        rule_id="mule_collection",
        conclusion=(
            "One account is aggregating funds from many otherwise unrelated senders in "
            "a short window, which is the classic collection/mule account shape."
        ),
        triggers=("fan_in",),
        weight=0.80,
        rationale=(
            "Legitimate merchant collection is usually spread over time and matched by "
            "corresponding orders; a tight fan-in without that context is anomalous.",
        ),
        next_steps=(
            "Verify whether the receiving account is a registered merchant with "
            "matching order records.",
            "Check whether the senders were themselves recent victims of fraud "
            "reports.",
        ),
    ),
    RootCauseRule(
        rule_id="dispersal",
        conclusion=(
            "One account dispersed funds to many recipients in a short window, "
            "consistent with rapid dissipation of illicit or misappropriated funds."
        ),
        triggers=("fan_out",),
        weight=0.78,
        rationale=(
            "Fan-out immediately after inbound value is the standard dissipation step "
            "that follows a compromise or a fraudulent credit.",
        ),
        next_steps=(
            "Trace the inbound funding of the dispersing account immediately before "
            "the fan-out.",
            "Attempt recall on the outbound legs that have not yet settled.",
        ),
    ),
    RootCauseRule(
        rule_id="material_outlier",
        conclusion=(
            "A single transfer is materially out of line with the rest of the case. "
            "This may be entirely legitimate (an invoice or netted settlement) but it "
            "carries the case's financial exposure."
        ),
        triggers=("amount_outlier",),
        weight=0.58,
        rationale=(
            "Amount alone is weak evidence of wrongdoing; it is reported because it "
            "concentrates risk, not because it is inherently suspicious.",
        ),
        next_steps=(
            "Obtain the underlying invoice, contract or settlement instruction for the "
            "outlying transfer.",
            "Compare against the sender's own 90-day amount distribution rather than "
            "this case only.",
        ),
    ),
    RootCauseRule(
        rule_id="internal_movement",
        conclusion=(
            "The activity is best explained as internal account movement or currency "
            "handling by the same party rather than a transfer of economic value to a "
            "third party."
        ),
        triggers=("self_transfer", "multi_currency_account"),
        weight=0.45,
        rationale=(
            "Same-party or multi-currency movement is routine in treasury and wallet "
            "products and is only meaningful alongside a stronger signal.",
        ),
        next_steps=(
            "Confirm the account ownership mapping to establish whether the two legs "
            "belong to the same beneficial owner.",
        ),
    ),
)

#: Evidence requests that apply whenever the data itself is the limitation.
_DATA_GAP_STEPS: tuple[str, ...] = (
    "Re-export the transactions with complete timestamp, amount, currency and status "
    "fields so timing and distribution analysis can run.",
    "Widen the evidence window around the incident to establish a behavioural "
    "baseline for the accounts involved.",
    "Provide the account/KYC records for the entities named in this report.",
)


@dataclass(slots=True)
class RootCauseResult:
    """Outcome of the reasoning stage."""

    primary: Hypothesis
    alternatives: list[Hypothesis] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


def _observation_lines(matched: Sequence[DetectedPattern]) -> list[str]:
    """Render each supporting pattern as a cited observation line."""
    lines: list[str] = []
    for pattern in matched:
        citation = ", ".join(pattern.transaction_ids[:5])
        if len(pattern.transaction_ids) > 5:
            citation += f", +{len(pattern.transaction_ids) - 5} more"
        lines.append(
            f"Observed [{pattern.pattern_id}] {pattern.description} "
            f"(detector confidence {pattern.confidence:.2f}"
            + (f"; evidence: {citation}" if citation else "")
            + ")"
        )
    return lines


def _fallback_hypothesis(
    analysis: TransactionAnalysis,
    quality: DataQuality | None,
) -> Hypothesis:
    """Hypothesis used when no pattern fired at all."""
    confidence, breakdown = score_confidence((), quality)
    parsed = quality.records_parsed if quality else analysis.transaction_count

    if parsed == 0:
        return Hypothesis(
            conclusion=(
                "No conclusion can be drawn: the submitted evidence contained no "
                "usable transaction records."
            ),
            confidence=min(confidence, 0.1),
            reasoning=[
                "Validation rejected or found nothing in the payload, so no analysis "
                "could be performed.",
                f"Confidence model: {breakdown['formula']} → {confidence}",
            ],
            grade=EvidenceGrade.UNKNOWN,
        )

    if confidence < 0.5:
        return Hypothesis(
            conclusion=(
                "The evidence is insufficient to identify a root cause. No pattern "
                "reached its detection threshold, but the data is too sparse or "
                "incomplete to treat that as an all-clear."
            ),
            confidence=confidence,
            reasoning=[
                f"{parsed} record(s) were analysed with "
                f"{(quality.field_completeness if quality else 0):.0%} core-field "
                f"completeness.",
                "Absence of a detected pattern under these conditions is weak "
                "evidence of absence.",
                f"Confidence model: {breakdown['formula']} → {confidence}",
            ],
            grade=EvidenceGrade.UNKNOWN,
        )

    completeness = quality.field_completeness if quality else 0.0
    well_supported = completeness >= 0.85
    return Hypothesis(
        conclusion=(
            "No suspicious activity was identified. The transactions are internally "
            "consistent: no duplicates, no state contradictions and no anomalous "
            "timing or amounts."
            + (
                ""
                if well_supported
                else " The evidence is incomplete, so this is a qualified rather than "
                "a clean result."
            )
        ),
        confidence=confidence,
        reasoning=[
            f"All {parsed} record(s) passed every detector in the pattern library.",
            (
                f"Core-field completeness was {completeness:.0%}, so the negative "
                f"result is well supported."
                if well_supported
                else f"Core-field completeness was only {completeness:.0%}; a pattern "
                f"hidden in the missing fields would not have been detected."
            ),
            f"Confidence model: {breakdown['formula']} → {confidence}",
        ],
        grade=(
            EvidenceGrade.STRONG_EVIDENCE if well_supported else EvidenceGrade.HYPOTHESIS
        ),
    )


def analyze_root_cause(
    transactions: Sequence[Transaction],
    analysis: TransactionAnalysis,
    patterns: Sequence[DetectedPattern],
    quality: DataQuality | None = None,
    config: InvestigationConfig | None = None,
) -> RootCauseResult:
    """Rank candidate root causes against the detected patterns."""
    config = config or InvestigationConfig()
    usable = [p for p in patterns if p.pattern_id != "detector_error"]
    present = {p.pattern_id for p in usable}

    if not usable:
        primary = _fallback_hypothesis(analysis, quality)
        steps = (
            [
                "No action required beyond routine monitoring; retain this report for "
                "the audit trail."
            ]
            if primary.grade is EvidenceGrade.STRONG_EVIDENCE
            else list(_DATA_GAP_STEPS)
        )
        return RootCauseResult(primary=primary, alternatives=[], next_steps=steps)

    scored: list[tuple[float, Hypothesis, RootCauseRule]] = []
    for rule in RULES:
        if not rule.matches(present):
            continue
        matched = [p for p in usable if p.pattern_id in rule.triggers]
        if not matched:
            continue

        # The "account_compromise" rule needs a burst plus a corroborating signal;
        # a burst on its own is explained better by automated_batch.
        if rule.rule_id == "account_compromise" and not (
            present & {"fan_out", "amount_outlier", "off_hours_activity", "pass_through_chain"}
        ):
            continue

        base_confidence, breakdown = score_confidence(matched, quality)
        confidence = round(min(0.95, base_confidence * rule.weight), 3)

        contradictions: list[str] = []
        for weakener in rule.weakened_by:
            if weakener in present:
                offender = next(p for p in usable if p.pattern_id == weakener)
                contradictions.append(
                    f"[{weakener}] {offender.description} — this argues against the "
                    f"conclusion above."
                )
        if contradictions:
            confidence = round(max(0.05, confidence - 0.10 * len(contradictions)), 3)

        txn_ids: list[str] = []
        for pattern in matched:
            for txn_id in pattern.transaction_ids:
                if txn_id not in txn_ids:
                    txn_ids.append(txn_id)

        strongest = max_severity([p.severity for p in matched])
        hypothesis = Hypothesis(
            conclusion=rule.conclusion,
            confidence=confidence,
            reasoning=[
                *_observation_lines(matched),
                *rule.rationale,
                f"Confidence model: {breakdown['formula']} "
                f"(pattern_strength={breakdown['pattern_strength']}, "
                f"data_quality={breakdown['data_quality']}, "
                f"corroboration={breakdown['corroboration']}) × rule prior "
                f"{rule.weight} → {confidence}",
            ],
            supporting_pattern_ids=sorted({p.pattern_id for p in matched}),
            supporting_transaction_ids=txn_ids[:20],
            contradicting_observations=contradictions,
            grade=(
                EvidenceGrade.STRONG_EVIDENCE
                if confidence >= 0.7 and strongest in (Severity.HIGH, Severity.CRITICAL)
                else EvidenceGrade.HYPOTHESIS
            ),
            provenance=Provenance.DETERMINISTIC,
        )
        scored.append((confidence, hypothesis, rule))

    if not scored:
        primary = _fallback_hypothesis(analysis, quality)
        return RootCauseResult(primary=primary, alternatives=[], next_steps=list(_DATA_GAP_STEPS))

    scored.sort(key=lambda row: (-row[0], row[2].rule_id))
    primary_confidence, primary, primary_rule = scored[0]
    alternatives = [h for _, h, _ in scored[1 : 1 + config.max_alternative_hypotheses]]

    # Recommendations: the leading explanation's checks first, then the runners-up.
    steps: list[str] = []
    for _, _, rule in scored[: 1 + config.max_alternative_hypotheses]:
        for step in rule.next_steps:
            if step not in steps:
                steps.append(step)

    needs_more_data = (
        quality is not None
        and (quality.field_completeness < 0.85 or quality.conflicting_record_count > 0)
    ) or primary_confidence < 0.6
    if needs_more_data:
        for step in _DATA_GAP_STEPS:
            if step not in steps:
                steps.append(step)

    return RootCauseResult(primary=primary, alternatives=alternatives, next_steps=steps[:10])
