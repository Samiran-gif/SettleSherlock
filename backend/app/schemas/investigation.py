"""Schemas for the deterministic investigation engine."""

from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class SettlementStatus(str, Enum):
    """The conclusions the investigation engine can reach."""

    SETTLED = "SETTLED"
    FAILED = "FAILED"
    DELAYED = "DELAYED"
    INCOMPLETE = "INCOMPLETE"
    NEEDS_INVESTIGATION = "NEEDS_INVESTIGATION"


class InvestigationResponse(BaseModel):
    """The outcome of investigating one transaction across all three systems."""

    transaction_id: str = Field(..., examples=["TXN10002"])
    status: SettlementStatus = Field(..., examples=["DELAYED"])
    root_cause: str = Field(..., examples=["Bank-side settlement delay"])

    investigation_confidence: int = Field(
        ...,
        ge=0,
        le=100,
        description=(
            "How complete and consistent the available evidence is, 0-100. "
            "This is not a financial risk score."
        ),
        examples=[94],
    )

    evidence: List[str] = Field(
        default_factory=list,
        description="Facts read directly off the gateway, bank and ledger records.",
    )
    exceptions: List[str] = Field(
        default_factory=list,
        description="Anomalies that need follow-up. Empty when there are none.",
    )
    recommended_action: str = Field(
        ..., examples=["Verify bank settlement batch and bank reference."]
    )
