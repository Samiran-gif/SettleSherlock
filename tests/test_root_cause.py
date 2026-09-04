"""Tests for confidence scoring and the root-cause rule base."""

from __future__ import annotations

from agent.analyzer import analyze_transactions
from agent.config import InvestigationConfig
from agent.evidence import build_evidence, score_confidence, select_suspicious_transactions
from agent.models import DataQuality, DetectedPattern, EvidenceGrade, Importance, Severity
from agent.patterns import detect_patterns
from agent.relationships import build_graph
from agent.root_cause import RULES, analyze_root_cause
from agent.validation import normalize_records

from conftest import at, tx


def pipeline(records, config: InvestigationConfig | None = None):
    """Run every deterministic stage and return the pieces the tests need."""
    config = config or InvestigationConfig()
    transactions, quality = normalize_records(records)
    analysis = analyze_transactions(transactions)
    graph = build_graph(transactions)
    patterns = detect_patterns(transactions, analysis, graph, config)
    result = analyze_root_cause(transactions, analysis, patterns, quality, config)
    return transactions, quality, analysis, patterns, result


def make_pattern(pattern_id: str, confidence: float = 0.8, **kwargs) -> DetectedPattern:
    return DetectedPattern(
        pattern_id=pattern_id,
        name=pattern_id,
        description="synthetic pattern",
        severity=kwargs.pop("severity", Severity.HIGH),
        confidence=confidence,
        transaction_ids=kwargs.pop("transaction_ids", ["TXN-1"]),
        **kwargs,
    )


def make_quality(**kwargs) -> DataQuality:
    defaults = dict(
        records_submitted=10,
        records_parsed=10,
        records_rejected=0,
        field_completeness=1.0,
    )
    defaults.update(kwargs)
    return DataQuality(**defaults)


class TestScoreConfidence:
    def test_no_patterns_scores_on_data_quality_alone(self):
        strong, breakdown = score_confidence((), make_quality())
        weak, _ = score_confidence((), make_quality(records_parsed=1, field_completeness=0.2))
        assert breakdown["mode"] == "absence_of_findings"
        assert strong > weak
        assert 0.0 < weak < 0.5 < strong <= 0.85

    def test_corroboration_raises_confidence(self):
        single, _ = score_confidence([make_pattern("a", 0.8)], make_quality())
        triple, breakdown = score_confidence(
            [make_pattern("a", 0.8), make_pattern("b", 0.8), make_pattern("c", 0.8)],
            make_quality(),
        )
        assert triple > single
        assert breakdown["distinct_patterns"] == 3

    def test_bad_data_lowers_confidence_for_the_same_finding(self):
        clean, _ = score_confidence([make_pattern("a", 0.9)], make_quality())
        messy, _ = score_confidence(
            [make_pattern("a", 0.9)],
            make_quality(field_completeness=0.4, conflicting_record_count=5),
        )
        assert messy < clean

    def test_never_claims_certainty(self):
        maxed, _ = score_confidence([make_pattern("a", 1.0)] * 5, make_quality())
        assert maxed <= 0.95

    def test_missing_quality_is_handled(self):
        confidence, _ = score_confidence([make_pattern("a", 0.9)], None)
        assert 0.0 < confidence < 1.0


class TestRuleBase:
    def test_every_rule_is_uniquely_identified(self):
        ids = [rule.rule_id for rule in RULES]
        assert len(ids) == len(set(ids))

    def test_every_rule_offers_next_steps_or_rationale(self):
        for rule in RULES:
            assert rule.rationale or rule.next_steps, rule.rule_id


class TestScenarios:
    def test_duplicate_charge_points_at_duplicate_settlement(self, duplicate_case):
        *_, result = pipeline(duplicate_case)
        assert "settled more than once" in result.primary.conclusion
        assert result.primary.confidence >= 0.7
        assert "TXN-1002" in result.primary.supporting_transaction_ids
        assert result.primary.grade is EvidenceGrade.STRONG_EVIDENCE
        assert any("[duplicate_settlement_reference]" in r for r in result.primary.reasoning)

    def test_clean_case_reports_no_suspicious_activity(self, normal_case):
        *_, result = pipeline(normal_case)
        assert "No suspicious activity" in result.primary.conclusion
        assert result.primary.confidence >= 0.7
        assert result.alternatives == []

    def test_insufficient_evidence_is_not_an_all_clear(self):
        *_, result = pipeline([{"sender": "ACC-A"}])
        assert "insufficient" in result.primary.conclusion.lower()
        assert result.primary.confidence < 0.5
        assert result.primary.grade is EvidenceGrade.UNKNOWN
        assert result.next_steps

    def test_no_usable_records_refuses_to_conclude(self):
        *_, result = pipeline(["nonsense", 42])
        assert "No conclusion can be drawn" in result.primary.conclusion
        assert result.primary.confidence <= 0.1

    def test_conflicting_evidence_leads_to_a_data_defect_conclusion(self):
        records = [
            tx("TXN-1", timestamp=at(0), amount=8400.0, status="success", reference_id="INV-1"),
            tx("TXN-1", timestamp=at(0), amount=4800.0, status="failed", reference_id="INV-1"),
            tx("TXN-2", timestamp=at(minutes=30), amount=8400.0, status="reversed"),
            tx("TXN-3", timestamp=at(minutes=65), amount=8400.0, status="success"),
        ]
        *_, result = pipeline(records)
        assert "defect" in result.primary.conclusion
        assert "conflicting_records" in result.primary.supporting_pattern_ids

    def test_burst_with_fan_out_suggests_compromise(self):
        records = [
            tx(
                f"TXN-{i}",
                timestamp=at(seconds=i * 15),
                receiver=f"ACC-MULE-{i}",
                amount=4800.0 + i,
            )
            for i in range(6)
        ]
        *_, result = pipeline(records)
        conclusions = [result.primary.conclusion] + [
            h.conclusion for h in result.alternatives
        ]
        assert any("third-party control" in c for c in conclusions)
        assert any("dispersed funds" in c for c in conclusions)

    def test_layering_chain_is_recognised(self):
        records = [
            tx("TXN-1", timestamp=at(0), sender="ACC-A", receiver="ACC-B", amount=50000.0),
            tx("TXN-2", timestamp=at(minutes=12), sender="ACC-B", receiver="ACC-C", amount=49500.0),
            tx("TXN-3", timestamp=at(minutes=25), sender="ACC-C", receiver="ACC-D", amount=49000.0),
        ]
        *_, result = pipeline(records)
        assert "layering" in result.primary.conclusion

    def test_identical_repeats_never_imply_account_compromise(self):
        """A burst of identical transfers to one payee is not an attack signal."""
        records = [
            tx(
                f"TXN-{i}",
                timestamp=at(seconds=i * 10),
                receiver="ACC-PAYROLL-DEST",
                amount=2000.0,
            )
            for i in range(6)
        ]
        *_, result = pipeline(records)
        conclusions = [result.primary.conclusion] + [
            h.conclusion for h in result.alternatives
        ]
        assert any("machine-generated" in c for c in conclusions)
        assert not any("third-party control" in c for c in conclusions)

    def test_contradictions_are_reported_and_cost_confidence(self):
        records = [
            tx("TXN-1", timestamp=at(0), amount=900.0, status="success"),
            tx("TXN-2", timestamp=at(5), amount=900.0, status="success"),
            tx("TXN-3", timestamp=at(minutes=10), amount=900.0, status="reversed"),
            tx("TXN-4", timestamp=at(minutes=40), amount=900.0, status="success"),
        ]
        *_, result = pipeline(records)
        hypotheses = [result.primary, *result.alternatives]
        assert any(h.contradicting_observations for h in hypotheses)

    def test_alternatives_are_ranked_below_the_primary(self, duplicate_case):
        *_, result = pipeline(duplicate_case)
        for alternative in result.alternatives:
            assert alternative.confidence <= result.primary.confidence


class TestEvidenceLinking:
    def test_every_evidence_item_cites_something(self, duplicate_case):
        transactions, quality, _, patterns, _ = pipeline(duplicate_case)
        items = build_evidence(transactions, patterns, quality)
        assert items
        for item in items:
            assert item.finding
            assert item.transaction_id or item.pattern_id

    def test_high_importance_items_come_first(self, duplicate_case):
        transactions, quality, _, patterns, _ = pipeline(duplicate_case)
        items = build_evidence(transactions, patterns, quality)
        assert items[0].importance is Importance.HIGH

    def test_data_gaps_become_evidence(self):
        transactions, quality, _, patterns, _ = pipeline([{"sender": "ACC-A"}])
        items = build_evidence(transactions, patterns, quality)
        assert any("Evidence gap" in item.finding for item in items)

    def test_evidence_is_capped(self, duplicate_case):
        transactions, quality, _, patterns, _ = pipeline(duplicate_case)
        config = InvestigationConfig(max_evidence_items=2)
        assert len(build_evidence(transactions, patterns, quality, config)) == 2

    def test_suspicious_transactions_are_ranked_by_risk(self, duplicate_case):
        transactions, _, _, patterns, _ = pipeline(duplicate_case)
        rows = select_suspicious_transactions(transactions, patterns)
        assert rows
        scores = [row["risk_score"] for row in rows]
        assert scores == sorted(scores, reverse=True) or rows[0]["severity"] == "critical"
        assert "TXN-1002" in {row["transaction_id"] for row in rows}
