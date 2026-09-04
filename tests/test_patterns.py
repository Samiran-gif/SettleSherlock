"""Tests for the deterministic pattern detectors."""

from __future__ import annotations

from agent.analyzer import analyze_transactions
from agent.config import InvestigationConfig
from agent.models import Severity
from agent.patterns import detect_patterns
from agent.relationships import build_graph
from agent.validation import normalize_records

from conftest import at, tx


def run(records, config: InvestigationConfig | None = None):
    """Detect patterns over ``records`` and index them by pattern id."""
    config = config or InvestigationConfig()
    transactions, _ = normalize_records(records)
    analysis = analyze_transactions(transactions)
    graph = build_graph(transactions)
    patterns = detect_patterns(transactions, analysis, graph, config)
    found: dict[str, list] = {}
    for pattern in patterns:
        found.setdefault(pattern.pattern_id, []).append(pattern)
    return found


class TestNoFalseAlarms:
    def test_normal_activity_is_clean(self, normal_case):
        found = run(normal_case)
        serious = {
            pattern_id
            for pattern_id, group in found.items()
            if any(p.severity in (Severity.HIGH, Severity.CRITICAL) for p in group)
        }
        assert serious == set(), f"unexpected serious findings: {serious}"

    def test_scheduled_subscription_is_low_severity(self):
        records = [
            tx(f"TXN-{i}", timestamp=at(days=30 * i), amount=15.99, reference_id=f"SUB-{i}")
            for i in range(4)
        ]
        found = run(records)
        assert "repeated_identical_amount" in found
        assert found["repeated_identical_amount"][0].severity is Severity.LOW
        assert "near_duplicate_transaction" not in found


class TestDuplication:
    def test_duplicate_settlement_under_one_reference(self, duplicate_case):
        found = run(duplicate_case)
        assert "duplicate_settlement_reference" in found
        pattern = found["duplicate_settlement_reference"][0]
        assert pattern.severity is Severity.CRITICAL
        assert set(pattern.transaction_ids) == {"TXN-1002", "TXN-1003"}
        assert pattern.metrics["successful_settlements"] == 2

    def test_near_duplicates_within_the_window(self, duplicate_case):
        found = run(duplicate_case)
        assert "near_duplicate_transaction" in found
        pattern = found["near_duplicate_transaction"][0]
        assert set(pattern.transaction_ids) == {"TXN-1002", "TXN-1003"}
        assert pattern.metrics["timestamps_complete"] is True

    def test_duplicates_outside_the_window_are_not_flagged(self):
        records = [
            tx("TXN-A", timestamp=at(0), amount=2500.0),
            tx("TXN-B", timestamp=at(hours=9), amount=2500.0),
        ]
        assert "near_duplicate_transaction" not in run(records)

    def test_missing_timestamps_lower_duplicate_confidence(self):
        with_time = run(
            [
                tx("TXN-A", timestamp=at(0), amount=2500.0),
                tx("TXN-B", timestamp=at(10), amount=2500.0),
            ]
        )["near_duplicate_transaction"][0]
        without_time = run(
            [
                tx("TXN-A", timestamp=None, amount=2500.0),
                tx("TXN-B", timestamp=None, amount=2500.0),
            ]
        )["near_duplicate_transaction"][0]
        assert without_time.confidence < with_time.confidence
        assert without_time.metrics["timestamps_complete"] is False


class TestStateMachine:
    def test_single_retry_after_failure_is_benign(self):
        found = run(
            [
                tx("TXN-1", timestamp=at(0), status="failed"),
                tx("TXN-2", timestamp=at(30), status="success"),
            ]
        )
        assert "retry_after_failure" in found
        assert found["retry_after_failure"][0].severity is Severity.LOW
        assert "double_charge_after_retry" not in found

    def test_two_successes_after_failure_is_a_double_charge(self, duplicate_case):
        found = run(duplicate_case)
        assert "double_charge_after_retry" in found
        assert found["double_charge_after_retry"][0].severity is Severity.CRITICAL

    def test_reversal_without_an_original(self):
        found = run([tx("TXN-1", status="reversed", amount=900.0)])
        assert "orphan_reversal" in found

    def test_reversal_with_a_matching_original_is_fine(self):
        found = run(
            [
                tx("TXN-1", timestamp=at(0), amount=900.0, status="success"),
                tx("TXN-2", timestamp=at(minutes=10), amount=900.0, status="reversed"),
            ]
        )
        assert "orphan_reversal" not in found

    def test_settlement_after_reversal(self):
        found = run(
            [
                tx("TXN-1", timestamp=at(0), amount=900.0, status="success"),
                tx("TXN-2", timestamp=at(minutes=10), amount=900.0, status="reversed"),
                tx("TXN-3", timestamp=at(minutes=40), amount=900.0, status="success"),
            ]
        )
        assert "settlement_after_reversal" in found

    def test_conflicting_records_for_one_id(self):
        found = run(
            [
                tx("TXN-1", amount=8400.0, status="success"),
                tx("TXN-1", amount=4800.0, status="failed"),
            ]
        )
        assert "conflicting_records" in found
        pattern = found["conflicting_records"][0]
        assert set(pattern.metrics["conflicting_fields"]) == {"amount", "status"}

    def test_shared_reference_with_mixed_states(self):
        found = run(
            [
                tx("TXN-1", timestamp=at(0), status="failed", reference_id="REF-1"),
                tx("TXN-2", timestamp=at(minutes=90), status="pending", reference_id="REF-1"),
            ]
        )
        assert "shared_reference_mixed_state" in found


class TestVelocityAndAmounts:
    def test_burst_is_detected(self):
        records = [
            tx(
                f"TXN-{i}",
                timestamp=at(seconds=i * 6),
                receiver=f"ACC-DEST-{i}",
                amount=1000.0 + i,
            )
            for i in range(6)
        ] + [tx("TXN-OLD", timestamp=at(days=-3), amount=90.0)]
        found = run(records)
        assert "transaction_burst" in found
        assert found["transaction_burst"][0].metrics["peak_count"] >= 4

    def test_spread_out_activity_is_not_a_burst(self):
        records = [tx(f"TXN-{i}", timestamp=at(hours=i * 6)) for i in range(6)]
        assert "transaction_burst" not in run(records)

    def test_large_amount_outlier(self):
        records = [tx(f"TXN-{i}", amount=100.0 + i) for i in range(6)]
        records.append(tx("TXN-BIG", amount=95000.0))
        found = run(records)
        assert "amount_outlier" in found
        assert found["amount_outlier"][0].transaction_ids == ["TXN-BIG"]

    def test_small_amounts_are_not_flagged_as_outliers(self):
        records = [tx(f"TXN-{i}", amount=5000.0 + i) for i in range(6)]
        records.append(tx("TXN-TINY", amount=1.5))
        assert "amount_outlier" not in run(records)

    def test_threshold_structuring(self):
        records = [
            tx(
                f"TXN-{i}",
                timestamp=at(hours=i * 3),
                amount=9400.0 + i * 50,
                receiver=f"ACC-DEST-{i}",
            )
            for i in range(4)
        ]
        found = run(records)
        assert "threshold_structuring" in found
        assert found["threshold_structuring"][0].metrics["threshold"] == 10000

    def test_off_hours_concentration(self):
        records = [
            tx(f"TXN-{i}", timestamp=at(hours=-8, seconds=i * 90), receiver=f"ACC-D-{i}")
            for i in range(4)
        ]
        found = run(records)
        assert "off_hours_activity" in found
        assert found["off_hours_activity"][0].severity is Severity.LOW


class TestGraphShapes:
    def test_fan_out_from_one_account(self):
        records = [
            tx(f"TXN-{i}", timestamp=at(seconds=i * 20), receiver=f"ACC-MULE-{i}", amount=4800.0 + i)
            for i in range(5)
        ]
        found = run(records)
        assert "fan_out" in found
        assert found["fan_out"][0].metrics["counterparty_count"] == 5

    def test_fan_out_ignores_unrelated_older_activity(self):
        records = [tx("TXN-OLD", timestamp=at(days=-40), receiver="MERCH-X", amount=10.0)]
        records += [
            tx(f"TXN-{i}", timestamp=at(seconds=i * 20), receiver=f"ACC-MULE-{i}", amount=4800.0)
            for i in range(5)
        ]
        assert "fan_out" in run(records)

    def test_fan_in_to_one_account(self):
        records = [
            tx(
                f"TXN-{i}",
                timestamp=at(seconds=i * 30),
                sender=f"ACC-PAYER-{i}",
                receiver="ACC-COLLECT-1",
                amount=1500.0 + i,
            )
            for i in range(5)
        ]
        assert "fan_in" in run(records)

    def test_pass_through_chain(self):
        records = [
            tx("TXN-1", timestamp=at(0), sender="ACC-A", receiver="ACC-B", amount=50000.0),
            tx("TXN-2", timestamp=at(minutes=12), sender="ACC-B", receiver="ACC-C", amount=49500.0),
            tx("TXN-3", timestamp=at(minutes=25), sender="ACC-C", receiver="ACC-D", amount=49000.0),
        ]
        found = run(records)
        assert "pass_through_chain" in found
        assert found["pass_through_chain"][0].metrics["path"][0] == "ACC-A"

    def test_chain_with_a_big_value_drop_is_not_pass_through(self):
        records = [
            tx("TXN-1", timestamp=at(0), sender="ACC-A", receiver="ACC-B", amount=50000.0),
            tx("TXN-2", timestamp=at(minutes=12), sender="ACC-B", receiver="ACC-C", amount=200.0),
            tx("TXN-3", timestamp=at(minutes=25), sender="ACC-C", receiver="ACC-D", amount=80.0),
        ]
        assert "pass_through_chain" not in run(records)

    def test_circular_flow(self):
        records = [
            tx("TXN-1", timestamp=at(0), sender="ACC-A", receiver="ACC-B", amount=5000.0),
            tx("TXN-2", timestamp=at(minutes=10), sender="ACC-B", receiver="ACC-A", amount=4900.0),
        ]
        found = run(records)
        assert "circular_flow" in found
        assert set(found["circular_flow"][0].metrics["cycle"]) == {"ACC-A", "ACC-B"}

    def test_self_transfer(self):
        found = run([tx("TXN-1", sender="ACC-A", receiver="ACC-A", amount=1000.0)])
        assert "self_transfer" in found


class TestRouteAndCurrency:
    def test_gateway_failure_cluster(self):
        records = [
            tx(f"TXN-F{i}", timestamp=at(hours=i), gateway="Wobbly", status="failed", receiver=f"M-{i}")
            for i in range(4)
        ] + [
            tx(f"TXN-S{i}", timestamp=at(hours=i + 10), gateway="Solid", status="success", receiver=f"M-{i}")
            for i in range(4)
        ]
        found = run(records)
        assert "processor_failure_cluster" in found
        assert found["processor_failure_cluster"][0].metrics["name"] == "Wobbly"

    def test_multi_currency_account(self):
        records = [
            tx("TXN-1", timestamp=at(0), currency="USD", amount=100.0),
            tx("TXN-2", timestamp=at(hours=6), currency="EUR", amount=250.0),
        ]
        assert "multi_currency_account" in run(records)


class TestRobustness:
    def test_empty_evidence_produces_no_patterns(self):
        assert run([]) == {}

    def test_detectors_survive_records_with_almost_no_data(self):
        found = run([{"sender": "ACC-A"}, {"amount": "???"}, {}])
        assert "detector_error" not in found

    def test_custom_config_changes_sensitivity(self):
        records = [
            tx("TXN-A", timestamp=at(0), amount=2500.0),
            tx("TXN-B", timestamp=at(minutes=30), amount=2500.0),
        ]
        assert "near_duplicate_transaction" not in run(records)
        loose = InvestigationConfig(duplicate_window_seconds=7200)
        assert "near_duplicate_transaction" in run(records, loose)
