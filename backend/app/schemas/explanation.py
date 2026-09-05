"""Schemas for the AI explanation layer."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.investigation import InvestigationResponse


class ExplanationSource(str, Enum):
    """Where the explanation text came from."""

    AI = "ai"
    FALLBACK = "fallback"


class ExplanationResponse(BaseModel):
    """A plain-English explanation of a deterministic investigation result.

    The ``investigation`` field is always the full, unmodified engine output —
    it remains the source of truth. ``explanation`` only re-words those same
    facts for a support agent, and says so honestly when the model was not
    used.
    """

    transaction_id: str = Field(..., examples=["TXN10002"])

    explanation: str = Field(
        ...,
        description="Plain-English summary of the investigation result.",
        examples=[
            "This transaction is delayed on the bank side. The gateway "
            "authorised it and the ledger recorded it, but the bank leg is "
            "still marked DELAYED."
        ],
    )

    source: ExplanationSource = Field(
        ...,
        description=(
            "'ai' when a model produced the text, 'fallback' when it was "
            "generated deterministically from the investigation result."
        ),
    )
    ai_available: bool = Field(
        ..., description="Whether the AI provider was reachable and used."
    )
    model: Optional[str] = Field(
        None,
        description="Model that produced the explanation, null on fallback.",
        examples=["llama-3.3-70b-versatile"],
    )
    notice: Optional[str] = Field(
        None,
        description=(
            "Why the AI was not used. Null when the explanation is from AI."
        ),
        examples=["AI explanation is unavailable: AI_API_KEY is not configured."],
    )

    investigation: InvestigationResponse = Field(
        ...,
        description=(
            "The deterministic investigation result, always returned "
            "unmodified alongside the explanation."
        ),
    )
