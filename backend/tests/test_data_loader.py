"""Tests for the CSV data loader, including its graceful-failure behaviour."""

from app.services.data_loader import DataLoader

GATEWAY_HEADER = "transaction_id,amount,status,timestamp,gateway_reference\n"


def test_loads_the_real_mock_csv_files():
    """The bundled CSVs load regardless of the current working directory."""
    loader = DataLoader()
    loader.load()

    record = loader.get_record("gateway", "TXN10001")
    assert record is not None
    assert record.amount == 2500.00
    assert record.status == "SUCCESS"
    assert record.reference == "GW10001"


def test_find_transaction_always_returns_all_three_keys():
    loader = DataLoader()

    records = loader.find_transaction("TXN10006")
    assert set(records) == {"gateway", "bank", "ledger"}
    assert records["bank"] is None


def test_unknown_transaction_returns_all_none():
    loader = DataLoader()

    assert loader.find_transaction("TXN99999") == {
        "gateway": None,
        "bank": None,
        "ledger": None,
    }


def test_missing_csv_files_do_not_raise(tmp_path):
    """An empty data directory yields empty sources rather than an error."""
    loader = DataLoader(data_dir=tmp_path)
    loader.load()

    assert loader.find_transaction("TXN10001") == {
        "gateway": None,
        "bank": None,
        "ledger": None,
    }


def test_malformed_rows_are_skipped_but_good_rows_still_load(tmp_path):
    """A bad row must not cost us the rest of the file."""
    (tmp_path / "gateway.csv").write_text(
        GATEWAY_HEADER
        + "TXN20001,1000.00,SUCCESS,2026-08-30T09:00:00,GW20001\n"
        + "TXN20002,not-a-number,SUCCESS,2026-08-30T09:05:00,GW20002\n"
        + "TXN20003,1500.00,SUCCESS,not-a-timestamp,GW20003\n"
        + ",1500.00,SUCCESS,2026-08-30T09:10:00,GW20004\n"
        + "TXN20005,2000.00,SUCCESS,2026-08-30T09:15:00,GW20005\n",
        encoding="utf-8",
    )
    loader = DataLoader(data_dir=tmp_path)
    loader.load()

    assert loader.get_record("gateway", "TXN20001") is not None
    assert loader.get_record("gateway", "TXN20005") is not None
    assert loader.get_record("gateway", "TXN20002") is None
    assert loader.get_record("gateway", "TXN20003") is None
