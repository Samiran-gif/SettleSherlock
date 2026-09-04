"""Tests for the deterministic statistical layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agent.analyzer import analyze_transactions, robust_z_scores, sliding_window_peak
from agent.validation import normalize_records

from conftest import at, tx


def _analyze(records):
    transactions, _ = normalize_records(records)
    return analyze_transactions(transactions)


class TestRobustZScores:
    def test_small_samples_are_not_scored(self):
        assert robust_z_scores([1.0, 2.0]) == [0.0, 0.0]

    def test_flags_the_single_large_value(self):
        scores = robust_z_scores([100.0, 102.0, 98.0, 101.0, 99.0, 50000.0])
        assert scores[-1] > 3.5
        assert all(abs(s) < 3.5 for s in scores[:-1])

    def test_identical_values_score_zero(self):
        assert robust_z_scores([5.0] * 6) == [0.0] * 6


class TestSlidingWindowPeak:
    def test_finds_the_densest_window(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        stamps = [base + timedelta(seconds=s) for s in (0, 3600, 7200, 7210, 7220, 7230)]
        peak, start, end = sliding_window_peak(stamps, timedelta(seconds=60))
        assert peak == 4
        assert (end - start).total_seconds() == 30

    def test_empty_input(self):
        assert sliding_window_peak([], timedelta(seconds=60)) == (0, None, None)


class TestAnalyzeTransactions:
    def test_amount_statistics(self):
        analysis = _analyze(
            [tx(f"TXN-{i}", amount=value) for i, value in enumerate([10, 20, 30, 40, 50])]
        )
        amounts = analysis.amounts
        assert amounts.count == 5
        assert amounts.total == Decimal("150")
        assert amounts.minimum == Decimal("10")
        assert amounts.maximum == Decimal("50")
        assert amounts.median == 30.0

    def test_status_rates(self):
        analysis = _analyze(
            [
                tx("TXN-1", status="success"),
                tx("TXN-2", status="failed"),
                tx("TXN-3", status="failed"),
                tx("TXN-4", status="reversed"),
            ]
        )
        assert analysis.failure_rate == 0.5
        assert analysis.reversal_rate == 0.25
        assert analysis.status_counts["failed"] == 2

    def test_timing_summary(self):
        analysis = _analyze(
            [
                tx("TXN-1", timestamp=at(0)),
                tx("TXN-2", timestamp=at(60)),
                tx("TXN-3", timestamp=at(180)),
            ]
        )
        assert analysis.timing.span_seconds == 180
        assert analysis.timing.min_gap_seconds == 60
        assert analysis.timing.missing_timestamps == 0

    def test_missing_timestamps_are_counted_not_fatal(self):
        analysis = _analyze([tx("TXN-1"), tx("TXN-2", timestamp=None)])
        assert analysis.timing.missing_timestamps == 1
        assert analysis.timing.first_event is not None

    def test_reference_and_id_groupings(self):
        analysis = _analyze(
            [
                tx("TXN-1", reference_id="REF-A"),
                tx("TXN-2", reference_id="REF-A"),
                tx("TXN-3", reference_id="REF-B"),
                tx("TXN-3", reference_id="REF-B"),
            ]
        )
        assert analysis.reference_groups["REF-A"] == ["TXN-1", "TXN-2"]
        assert "TXN-3" in analysis.duplicate_id_groups

    def test_per_gateway_status_rollup(self):
        analysis = _analyze(
            [
                tx("TXN-1", gateway="Alpha", status="failed"),
                tx("TXN-2", gateway="Alpha", status="failed"),
                tx("TXN-3", gateway="Beta", status="success"),
            ]
        )
        assert analysis.per_gateway_status["Alpha"]["failed"] == 2
        assert analysis.per_gateway_status["Beta"]["success"] == 1

    def test_empty_case_does_not_crash(self):
        analysis = _analyze([])
        assert analysis.transaction_count == 0
        assert analysis.amounts.count == 0
        assert analysis.to_dict()["failure_rate"] == 0.0
