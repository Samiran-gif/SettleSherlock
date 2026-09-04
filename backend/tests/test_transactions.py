"""Tests for the transaction lookup endpoint, backed by the mock CSVs."""

from app.core.config import settings

TRANSACTIONS_URL = f"{settings.API_V1_PREFIX}/transactions"


def test_normal_settlement_returns_all_three_systems(client):
    """TXN10001 is a clean settlement present in gateway, bank and ledger."""
    response = client.get(f"{TRANSACTIONS_URL}/TXN10001")
    assert response.status_code == 200

    body = response.json()
    assert body["transaction_id"] == "TXN10001"
    assert body["gateway"] is not None
    assert body["bank"] is not None
    assert body["ledger"] is not None


def test_delayed_bank_settlement_returns_all_three_systems(client):
    """TXN10002 exists everywhere, but the bank leg is DELAYED."""
    response = client.get(f"{TRANSACTIONS_URL}/TXN10002")
    assert response.status_code == 200

    body = response.json()
    assert body["gateway"]["status"] == "SUCCESS"
    assert body["bank"]["status"] == "DELAYED"
    assert body["ledger"]["status"] == "RECORDED"


def test_missing_ledger_record_returns_null_ledger(client):
    """TXN10003 has no ledger row, which must not be a 404."""
    response = client.get(f"{TRANSACTIONS_URL}/TXN10003")
    assert response.status_code == 200

    body = response.json()
    assert body["gateway"] is not None
    assert body["bank"] is not None
    assert body["ledger"] is None


def test_missing_bank_record_returns_null_bank(client):
    """TXN10006 has no bank row, which must not be a 404."""
    response = client.get(f"{TRANSACTIONS_URL}/TXN10006")
    assert response.status_code == 200

    body = response.json()
    assert body["gateway"] is not None
    assert body["bank"] is None
    assert body["ledger"] is not None


def test_unknown_transaction_returns_404(client):
    """A transaction absent from all three systems is a 404."""
    response = client.get(f"{TRANSACTIONS_URL}/TXN99999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Transaction TXN99999 not found"}


def test_values_are_loaded_from_the_csv_files(client):
    """Amounts, statuses, timestamps and references come straight from the CSVs."""
    response = client.get(f"{TRANSACTIONS_URL}/TXN10002")
    body = response.json()

    assert body["gateway"] == {
        "transaction_id": "TXN10002",
        "amount": 5000.0,
        "status": "SUCCESS",
        "timestamp": "2026-08-30T10:32:00",
        "reference": "GW10002",
    }
    assert body["bank"]["amount"] == 5000.0
    assert body["bank"]["reference"] == "BANK10002"
    assert body["ledger"]["timestamp"] == "2026-08-30T10:35:00"


def test_amount_mismatch_is_reported_as_stored(client):
    """TXN10004 has differing amounts per system; the API must not reconcile them."""
    body = client.get(f"{TRANSACTIONS_URL}/TXN10004").json()

    assert body["gateway"]["amount"] == 7500.0
    assert body["bank"]["amount"] == 7000.0
    assert body["ledger"]["amount"] == 7500.0


def test_non_decimal_amounts_are_preserved(client):
    """TXN10003 uses a fractional amount, so parsing must not round to int."""
    body = client.get(f"{TRANSACTIONS_URL}/TXN10003").json()

    assert body["gateway"]["amount"] == 1800.50
