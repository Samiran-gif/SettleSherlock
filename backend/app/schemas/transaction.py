"""Schemas for transaction lookups across the gateway, bank and ledger systems."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TransactionRecord(BaseModel):
    """One transaction as recorded by a single system.

    Each source CSV names its reference column differently
    (gateway_reference / bank_reference / ledger_reference); the data loader
    maps all of them onto the generic ``reference`` field.
    """

    transaction_id: str = Field(..., examples=["TXN10002"])
    amount: float = Field(..., examples=[5000.0])
    status: str = Field(..., examples=["SUCCESS"])
    timestamp: datetime = Field(..., examples=["2026-08-30T10:32:00"])
    reference: str = Field(..., examples=["GW10002"])


class TransactionLookupResponse(BaseModel):
    """A transaction seen from all three systems.

    A system's field is ``null`` when that system has no record of the
    transaction, which is exactly what the investigation stage needs to detect.
    """

    transaction_id: str = Field(..., examples=["TXN10002"])
    gateway: Optional[TransactionRecord] = None
    bank: Optional[TransactionRecord] = None
    ledger: Optional[TransactionRecord] = None
