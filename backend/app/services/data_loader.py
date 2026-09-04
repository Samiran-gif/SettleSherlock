"""Loads the mock settlement CSVs and looks transactions up by id.

The three source systems live in ``app/data`` as CSV files. This module reads
them with the standard library ``csv`` module and keeps them in memory, since
the hackathon dataset is tiny.

Robustness rules (deliberate, the investigation stage depends on them):
  * a missing CSV file yields an empty source instead of an error
  * a malformed row is skipped and logged, the rest of the file still loads
  * a transaction missing from one system is not an error at all
"""

import csv
import logging
from pathlib import Path
from typing import Dict, Optional

from pydantic import ValidationError

from app.schemas.transaction import TransactionRecord

logger = logging.getLogger(__name__)

# app/services/data_loader.py -> app/ -> app/data
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Source name -> (csv file name, name of that file's reference column)
SOURCES: Dict[str, tuple] = {
    "gateway": ("gateway.csv", "gateway_reference"),
    "bank": ("bank.csv", "bank_reference"),
    "ledger": ("ledger.csv", "ledger_reference"),
}


class DataLoader:
    """Reads the settlement CSVs and serves transaction records from memory."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        # {"gateway": {"TXN10001": TransactionRecord, ...}, "bank": {...}, ...}
        self._records: Dict[str, Dict[str, TransactionRecord]] = {}

    def load(self) -> None:
        """Read every source CSV into memory, replacing anything loaded before."""
        self._records = {
            source: self._read_csv(file_name, reference_column)
            for source, (file_name, reference_column) in SOURCES.items()
        }
        loaded = {source: len(rows) for source, rows in self._records.items()}
        logger.info("Loaded settlement data from %s: %s", self.data_dir, loaded)

    def _ensure_loaded(self) -> None:
        """Load on first use so importing this module never touches the disk."""
        if not self._records:
            self.load()

    def _read_csv(
        self, file_name: str, reference_column: str
    ) -> Dict[str, TransactionRecord]:
        """Parse one CSV into ``{transaction_id: TransactionRecord}``."""
        path = self.data_dir / file_name
        records: Dict[str, TransactionRecord] = {}

        if not path.is_file():
            logger.warning("Data file %s is missing; treating source as empty", path)
            return records

        try:
            with path.open(newline="", encoding="utf-8") as csv_file:
                for line_number, row in enumerate(csv.DictReader(csv_file), start=2):
                    record = self._build_record(row, reference_column)
                    if record is None:
                        logger.warning("Skipping malformed row %s in %s", line_number, path)
                        continue
                    records[record.transaction_id] = record
        except OSError as error:
            logger.warning("Could not read %s (%s); treating source as empty", path, error)
            return {}

        return records

    @staticmethod
    def _build_record(
        row: Dict[str, Optional[str]], reference_column: str
    ) -> Optional[TransactionRecord]:
        """Turn one CSV row into a record, or return None if the row is unusable."""
        transaction_id = (row.get("transaction_id") or "").strip()
        if not transaction_id:
            return None

        try:
            return TransactionRecord(
                transaction_id=transaction_id,
                amount=(row.get("amount") or "").strip(),
                status=(row.get("status") or "").strip(),
                timestamp=(row.get("timestamp") or "").strip(),
                reference=(row.get(reference_column) or "").strip(),
            )
        except (ValidationError, AttributeError, TypeError):
            return None

    def get_record(self, source: str, transaction_id: str) -> Optional[TransactionRecord]:
        """Return one system's record for a transaction, or None if absent."""
        self._ensure_loaded()
        return self._records.get(source, {}).get(transaction_id.strip())

    def find_transaction(self, transaction_id: str) -> Dict[str, Optional[TransactionRecord]]:
        """Return every system's view of a transaction, using None where absent.

        The returned dict always has a ``gateway``, ``bank`` and ``ledger`` key,
        so callers never have to guard against a source being missing.
        """
        self._ensure_loaded()
        transaction_id = transaction_id.strip()
        return {
            source: self._records.get(source, {}).get(transaction_id)
            for source in SOURCES
        }


# Shared instance: the CSVs are read once and reused for every request.
data_loader = DataLoader()


def get_data_loader() -> DataLoader:
    """FastAPI dependency giving routes access to the shared loader."""
    return data_loader
