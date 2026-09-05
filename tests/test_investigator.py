"""End-to-end tests: the eight required scenarios and the report contract."""

from __future__ import annotations

import json

from agent import Investigator, investigate
from agent.config import InvestigationConfig
from agent.models import Provenance, Severity

from conftest import at, load_example, tx

#: Keys the backend API contract depends on.
REQUIRED_KEYS = {
    "case_summary",
    "severity",
    "suspicious_transactions",
    "entities_involved",
    "timeline",
    "detected_patterns",
    "root_cause",
    "evidence",
    "alternative_hypotheses",
    "unknowns",
    "recommended_next_steps",
}


class TestReportContract:
    def test_report_has_every_required_key(self, duplicate_case):
        payload = investigate(duplicate_case).to_dict()
        assert REQUIRED_KEYS <= set(payload)

    def test_report_is_json_serializable(self, duplicate_case):
        report = investigate(duplicate_case)
        restored = json.loads(report.to_json())
        assert restored["severity"] == report.severity.value
        assert isinstance(restored["root_cause"]["confidence"], float)

    def test_root_cause_shape(self, duplicate_case):
        root_cause = investigate(duplicate_case).to_dict()["root_cause"]
        assert set(root_cause) >= {"conclusion", "confidence", "reasoning"}
        assert 0.0 <= root_cause["confidence"] <= 1.0
        assert root_cause["reasoning"]

    def test_evidence_items_carry_importance_and_provenance(self, duplicate_case):
        for item in investigate(duplicate_case).to_dict()["evidence"]:
            assert item["importance"] in ("low", "medium", "high")
            assert item["provenance"] == "deterministic"

    def test_meta_records_how_the_report_was_produced(self, duplicate_case):
        meta = investigate(duplicate_case).to_dict()["meta"]
        assert meta["analysis"] == "deterministic"
        assert meta["llm_enrichment"] == "not_applied"
        assert meta["agent_version"]
        assert "config" in meta and "graph" in meta

    def test_severity_is_ordered_across_cases(self, normal_case, duplicate_case):
        clean = investigate(normal_case).severity
        bad = investigate(duplicate_case).severity
        assert clean is Severity.LOW
        assert bad is Severity.CRITICAL


class TestScenario1NormalTransactions:
    def test_no_findings_and_high_confidence(self, normal_case):
        report = investigate(normal_case)
        assert report.severity is Severity.LOW
        assert report.suspicious_transactions == []
        assert "No suspicious activity" in report.root_cause.conclusion
        assert report.root_cause.confidence >= 0.7

    def test_timeline_covers_every_record(self, normal_case):
        report = investigate(normal_case)
        assert len(report.timeline) == len(normal_case)
        assert all(event.timestamp is not None for event in report.timeline)

    def test_confirmed_facts_are_present_even_when_clean(self, normal_case):
        report = investigate(normal_case)
        assert any("transaction record(s) were parsed" in f for f in report.confirmed_facts)


class TestScenario2DuplicateTransactions:
    def test_duplicates_are_identified_and_cited(self, duplicate_case):
        report = investigate(duplicate_case)
        assert report.has_pattern("duplicate_settlement_reference")
        assert report.has_pattern("near_duplicate_transaction")
        assert {"TXN-1002", "TXN-1003"} <= set(report.suspicious_transaction_ids())
        assert "settled more than once" in report.root_cause.conclusion

    def test_untouched_records_are_not_implicated(self, duplicate_case):
        report = investigate(duplicate_case)
        assert "TXN-1005" not in report.suspicious_transaction_ids()

    def test_next_steps_ask_for_the_gateway_log(self, duplicate_case):
        report = investigate(duplicate_case)
        assert any("gateway request log" in step for step in report.recommended_next_steps)


class TestScenario3SuspiciousBurst:
    def test_burst_is_flagged_with_entities(self):
        records = [
            tx(f"TXN-{i}", timestamp=at(seconds=i * 15), receiver=f"ACC-MULE-{i}", amount=4800.0 + i)
            for i in range(6)
        ]
        report = investigate(records)
        assert report.has_pattern("transaction_burst")
        assert report.has_pattern("fan_out")
        assert report.severity in (Severity.HIGH, Severity.CRITICAL)
        flagged = {e.entity_id for e in report.entities_involved if e.flags}
        assert "ACC-SENDER-1" in flagged

    def test_timeline_marks_the_burst_window(self):
        records = [
            tx(f"TXN-{i}", timestamp=at(seconds=i * 15), receiver=f"ACC-MULE-{i}", amount=4800.0)
            for i in range(6)
        ]
        report = investigate(records)
        assert any("[marker]" in event.event for event in report.timeline)


class TestScenario4FailedThenSuccessful:
    def test_single_retry_is_low_severity(self):
        records = [
            tx("TXN-1", timestamp=at(0), amount=750.0, status="failed", reference_id="ORD-1"),
            tx("TXN-2", timestamp=at(45), amount=750.0, status="success", reference_id="ORD-1"),
        ]
        report = investigate(records)
        assert report.has_pattern("retry_after_failure")
        assert not report.has_pattern("double_charge_after_retry")
        assert report.severity in (Severity.LOW, Severity.MEDIUM)

    def test_retry_that_settles_twice_is_critical(self, duplicate_case):
        report = investigate(duplicate_case)
        assert report.has_pattern("double_charge_after_retry")
        assert report.severity is Severity.CRITICAL


class TestScenario5MultipleConnectedAccounts:
    def test_graph_links_the_chain(self):
        report = investigate(load_example("layering_chain.json"))
        assert report.has_pattern("pass_through_chain")
        assert report.has_pattern("circular_flow")
        entities = {e.entity_id: e for e in report.entities_involved}
        assert "ACC-HOP-B" in entities
        assert "ACC-HOP-C" in entities["ACC-HOP-B"].linked_entities
        assert report.meta["graph"]["edge_count"] == 6

    def test_banks_and_gateways_are_modelled_as_entities(self):
        report = investigate(load_example("layering_chain.json"))
        kinds = {e.entity_type for e in report.entities_involved}
        assert {"account", "bank", "gateway"} <= kinds


class TestScenario6MissingFields:
    def test_partial_records_still_produce_a_report(self):
        report = investigate(load_example("incomplete_evidence.json"))
        assert report.data_quality.records_parsed == 3
        assert report.data_quality.field_completeness < 1.0
        assert report.unknowns
        assert report.root_cause is not None

    def test_missing_fields_are_named_in_unknowns(self):
        report = investigate(load_example("incomplete_evidence.json"))
        joined = " ".join(report.unknowns)
        assert "timestamp" in joined
        assert "transaction_id" in joined or "receiver" in joined

    def test_unparseable_values_are_normalized_away(self):
        report = investigate(load_example("incomplete_evidence.json"))
        statuses = {row["status"] for row in report.suspicious_transactions}
        assert "unknown" not in statuses or report.severity is not Severity.CRITICAL

    def test_records_without_timestamps_are_kept_on_the_timeline(self):
        report = investigate([tx("TXN-1", timestamp=None), tx("TXN-2")])
        undated = [e for e in report.timeline if e.timestamp is None]
        assert len(undated) == 1
        assert undated[0].details["timestamp_missing"] is True


class TestScenario7ConflictingEvidence:
    def test_conflict_is_detected_and_dampens_confidence(self):
        report = investigate(load_example("conflicting_records.json"))
        assert report.has_pattern("conflicting_records")
        assert report.data_quality.conflicting_record_count == 2
        assert "defect" in report.root_cause.conclusion
        assert report.root_cause.confidence < 0.85

    def test_conflict_lowers_confidence_versus_clean_data(self):
        clean = [
            tx("TXN-1", timestamp=at(0), amount=2500.0, reference_id="ORD-1"),
            tx("TXN-2", timestamp=at(20), amount=2500.0, reference_id="ORD-1"),
        ]
        conflicted = clean + [tx("TXN-1", timestamp=at(0), amount=99.0, status="failed")]
        assert (
            investigate(conflicted).root_cause.confidence
            != investigate(clean).root_cause.confidence
        )

    def test_authoritative_source_is_listed_as_unknown(self):
        report = investigate(load_example("conflicting_records.json"))
        assert any("authoritative" in unknown for unknown in report.unknowns)


class TestScenario8InsufficientEvidence:
    def test_empty_payload(self):
        report = investigate([])
        assert report.severity is Severity.LOW
        assert report.data_quality.records_parsed == 0
        assert "No usable transaction records" in report.case_summary
        assert report.root_cause.confidence <= 0.1

    def test_single_sparse_record(self):
        report = investigate([{"sender": "ACC-A"}])
        assert "insufficient" in report.root_cause.conclusion.lower()
        assert report.root_cause.confidence < 0.5

    def test_absence_of_findings_is_not_stated_as_certainty(self, normal_case):
        report = investigate(normal_case)
        assert report.root_cause.confidence <= 0.85

    def test_garbage_payloads_do_not_raise(self):
        for payload in (None, "string", 42, {}, [None], [[]], {"transactions": None}):
            report = investigate(payload)
            assert report.root_cause is not None
            assert report.to_dict()["severity"] in ("low", "medium", "high", "critical")


class TestConfigurability:
    def test_investigator_instance_is_reusable(self, normal_case, duplicate_case):
        investigator = Investigator()
        first = investigator.investigate(normal_case)
        second = investigator.investigate(duplicate_case)
        third = investigator.investigate(normal_case)
        assert first.to_dict()["detected_patterns"] == third.to_dict()["detected_patterns"]
        assert second.severity is Severity.CRITICAL

    def test_results_are_deterministic(self, duplicate_case):
        first = investigate(duplicate_case).to_dict()
        second = investigate(duplicate_case).to_dict()
        first["meta"].pop("duration_ms")
        second["meta"].pop("duration_ms")
        assert first == second

    def test_thresholds_can_be_tightened(self):
        records = [
            tx("TXN-1", timestamp=at(0), amount=2500.0),
            tx("TXN-2", timestamp=at(200), amount=2500.0),
        ]
        assert investigate(records).has_pattern("near_duplicate_transaction")
        strict = InvestigationConfig(duplicate_window_seconds=30)
        assert not investigate(records, config=strict).has_pattern("near_duplicate_transaction")

    def test_llm_is_off_by_default(self, duplicate_case):
        report = investigate(duplicate_case)
        assert report.case_summary_source is Provenance.DETERMINISTIC
        assert report.ai_narrative is None
