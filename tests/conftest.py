"""Shared pytest fixtures and record builders.

No test in this suite requires network access or an API key: LLM enrichment is
off by default and the one test that exercises it injects a fake provider.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

# Make the package importable when pytest is run from any directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXAMPLES_DIR = PROJECT_ROOT / "examples"

#: Fixed clock so every test is deterministic.
BASE_TIME = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds: float = 0, minutes: float = 0, hours: float = 0, days: float = 0) -> str:
    """ISO-8601 timestamp offset from :data:`BASE_TIME`."""
    moment = BASE_TIME + timedelta(
        seconds=seconds, minutes=minutes, hours=hours, days=days
    )
    return moment.isoformat().replace("+00:00", "Z")


#: Sentinel meaning "use the default value" — distinct from an explicit None,
#: which omits the field entirely.
_DEFAULT = object()


def tx(
    transaction_id: str,
    *,
    timestamp: Any = _DEFAULT,
    sender: str = "ACC-SENDER-1",
    receiver: str = "MERCH-RECEIVER-1",
    amount: Any = 100.0,
    currency: str = "USD",
    status: str = "success",
    **extra: Any,
) -> dict[str, Any]:
    """Build a transaction record with sensible defaults.

    Pass ``None`` for any keyword to omit that field entirely, which is how the
    missing-data tests are written.
    """
    record: dict[str, Any] = {
        "transaction_id": transaction_id,
        "timestamp": at() if timestamp is _DEFAULT else timestamp,
        "sender": sender,
        "receiver": receiver,
        "amount": amount,
        "currency": currency,
        "status": status,
        "transaction_type": "transfer",
        "account_id": sender,
        "bank": "First Meridian",
        "gateway": "PayFlow",
    }
    record.update(extra)
    return {k: v for k, v in record.items() if v is not None}


def load_example(name: str) -> Any:
    """Load one of the shipped example payloads."""
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def normal_case() -> list[dict[str, Any]]:
    """Seven unremarkable payments from different customers, days apart."""
    return [
        tx(
            f"TXN-{index}",
            timestamp=at(days=index, minutes=index * 7),
            sender=f"ACC-CUST-{index}",
            receiver=f"MERCH-{index % 3}",
            amount=50.0 + index * 23.5,
            reference_id=f"ORD-{index}",
        )
        for index in range(1, 8)
    ]


@pytest.fixture
def duplicate_case() -> list[dict[str, Any]]:
    """One payment that failed once then settled twice under one reference."""
    return [
        tx("TXN-1001", timestamp=at(0), amount=2500.0, status="failed", reference_id="ORD-88231"),
        tx("TXN-1002", timestamp=at(12), amount=2500.0, reference_id="ORD-88231"),
        tx("TXN-1003", timestamp=at(41), amount=2500.0, reference_id="ORD-88231"),
        tx("TXN-1004", timestamp=at(minutes=5), sender="ACC-CUST-78", amount=189.99, reference_id="ORD-88232"),
        tx("TXN-1005", timestamp=at(minutes=19), sender="ACC-CUST-79", amount=74.5, reference_id="ORD-88233"),
        tx("TXN-1006", timestamp=at(hours=1), sender="ACC-CUST-80", amount=412.0, reference_id="ORD-88234"),
    ]
