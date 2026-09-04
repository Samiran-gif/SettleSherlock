"""Tests for ingestion, normalization and graceful handling of bad data."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from agent.models import TransactionStatus
from agent.validation import (
    normalize_records,
    normalize_status,
    parse_amount,
    parse_timestamp,
)

from conftest import at, tx


class TestParseAmount:
    def test_accepts_numbers_and_decimals(self):
        assert parse_amount(12.5) == Decimal("12.5")
        assert parse_amount(7) == Decimal("7")
        assert parse_amount(Decimal("3.30")) == Decimal("3.30")

    def test_accepts_formatted_strings(self):
        assert parse_amount("1,250.00") == Decimal("1250.00")
        assert parse_amount("$1250") == Decimal("1250")
        assert parse_amount("USD 99.95") == Decimal("99.95")

    def test_rejects_nonsense(self):
        for value in (None, "", "unknown", "n/a", True, [], {}):
            assert parse_amount(value) is None


class TestParseTimestamp:
    def test_iso_with_and_without_zulu(self):
        expected = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
        assert parse_timestamp("2026-08-14T10:00:00Z") == expected
        assert parse_timestamp("2026-08-14T10:00:00+00:00") == expected

    def test_naive_input_is_assumed_utc(self):
        assert parse_timestamp("2026-08-14 10:00:00").tzinfo is timezone.utc

    def test_offsets_are_converted_to_utc(self):
        parsed = parse_timestamp("2026-08-14T12:00:00+02:00")
        assert parsed == datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)

    def test_epoch_seconds_and_milliseconds_agree(self):
        seconds = parse_timestamp(1786000000)
        millis = parse_timestamp(1786000000000)
        assert seconds == millis

    def test_unparseable_returns_none(self):
        assert parse_timestamp("not-a-real-timestamp") is None
        assert parse_timestamp(None) is None


class TestNormalizeStatus:
    def test_status_families(self):
        assert normalize_status("Completed") is TransactionStatus.SUCCESS
        assert normalize_status("SETTLED") is TransactionStatus.SUCCESS
        assert normalize_status("declined") is TransactionStatus.FAILED
        assert normalize_status("in-progress") is TransactionStatus.PENDING
        assert normalize_status("chargeback") is TransactionStatus.REVERSED
        assert normalize_status("???") is TransactionStatus.UNKNOWN


class TestNormalizeRecords:
    def test_accepts_a_bare_list(self):
        transactions, quality = normalize_records([tx("TXN-1")])
        assert len(transactions) == 1
        assert quality.records_parsed == 1
        assert quality.records_rejected == 0

    def test_accepts_a_wrapped_payload(self):
        transactions, _ = normalize_records({"transactions": [tx("TXN-1"), tx("TXN-2")]})
        assert [t.transaction_id for t in transactions] == ["TXN-1", "TXN-2"]

    def test_accepts_a_single_record(self):
        transactions, _ = normalize_records(tx("TXN-1"))
        assert len(transactions) == 1

    def test_field_aliases_are_understood(self):
        transactions, _ = normalize_records(
            [
                {
                    "txn_id": "TXN-ALIAS",
                    "created_at": at(),
                    "from": "ACC-A",
                    "to": "ACC-B",
                    "value": "500",
                    "ccy": "eur",
                    "state": "approved",
                    "psp": "SwiftPay",
                    "idempotency_key": "REF-1",
                }
            ]
        )
        txn = transactions[0]
        assert txn.transaction_id == "TXN-ALIAS"
        assert txn.sender == "ACC-A"
        assert txn.amount == Decimal("500")
        assert txn.currency == "EUR"
        assert txn.status is TransactionStatus.SUCCESS
        assert txn.gateway == "SwiftPay"
        assert txn.reference_id == "REF-1"

    def test_missing_fields_are_recorded_not_fatal(self):
        transactions, quality = normalize_records(
            [{"sender": "ACC-A", "amount": "unknown", "status": "???"}]
        )
        txn = transactions[0]
        assert txn.synthetic_id is True
        assert txn.transaction_id.startswith("UNIDENTIFIED-")
        assert txn.amount is None
        assert txn.timestamp is None
        assert txn.status is TransactionStatus.UNKNOWN
        problems = {i.field for i in quality.issues}
        assert {"transaction_id", "timestamp", "amount", "receiver"} <= problems
        assert quality.field_completeness < 0.3

    def test_non_object_records_are_rejected(self):
        transactions, quality = normalize_records([tx("TXN-1"), "garbage", 42, None])
        assert len(transactions) == 1
        assert quality.records_rejected == 3
        assert quality.rejection_ratio == 0.75

    def test_empty_and_none_payloads_are_safe(self):
        for payload in ([], None):
            transactions, quality = normalize_records(payload)
            assert transactions == []
            assert quality.records_parsed == 0

    def test_string_payload_is_reported_as_a_payload_issue(self):
        transactions, quality = normalize_records("just a string")
        assert transactions == []
        assert any(i.field == "<payload>" for i in quality.issues)

    def test_conflicting_duplicate_ids_are_counted(self):
        transactions, quality = normalize_records(
            [tx("TXN-SAME", amount=100.0), tx("TXN-SAME", amount=250.0)]
        )
        assert len(transactions) == 2
        assert quality.conflicting_record_count == 2

    def test_identical_duplicate_ids_are_not_a_conflict(self):
        _, quality = normalize_records([tx("TXN-SAME"), tx("TXN-SAME")])
        assert quality.conflicting_record_count == 0

    def test_complete_record_scores_full_completeness(self):
        _, quality = normalize_records([tx("TXN-1")])
        assert quality.field_completeness == 1.0
        assert quality.missing_fields == {}
