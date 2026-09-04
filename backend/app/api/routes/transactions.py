"""Transaction lookup, investigation and explanation endpoints."""

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.common import ErrorResponse
from app.schemas.explanation import ExplanationResponse
from app.schemas.investigation import InvestigationResponse
from app.schemas.transaction import TransactionLookupResponse, TransactionRecord
from app.services.ai_explanation import (
    AIExplanationService,
    get_ai_explanation_service,
)
from app.services.data_loader import DataLoader, get_data_loader
from app.services.investigation import investigate

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _load_records_or_404(
    loader: DataLoader, transaction_id: str
) -> Dict[str, Optional[TransactionRecord]]:
    """Fetch a transaction's three records, 404-ing only if none of them exist.

    A transaction present in just one or two systems is a legitimate finding,
    not an error, so it is returned for the investigation engine to reason over.
    """
    records = loader.find_transaction(transaction_id)

    if not any(records.values()):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {transaction_id} not found",
        )

    return records


@router.get(
    "/{transaction_id}",
    response_model=TransactionLookupResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Look a transaction up across gateway, bank and ledger",
)
async def get_transaction(
    transaction_id: str,
    loader: DataLoader = Depends(get_data_loader),
) -> TransactionLookupResponse:
    """Return all three systems' records for one transaction.

    A system that has no record of the transaction comes back as ``null``.
    """
    records = _load_records_or_404(loader, transaction_id)

    return TransactionLookupResponse(
        transaction_id=transaction_id,
        gateway=records["gateway"],
        bank=records["bank"],
        ledger=records["ledger"],
    )


@router.get(
    "/{transaction_id}/investigation",
    response_model=InvestigationResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Investigate a transaction's settlement across all three systems",
)
async def get_transaction_investigation(
    transaction_id: str,
    loader: DataLoader = Depends(get_data_loader),
) -> InvestigationResponse:
    """Run the deterministic investigation engine over one transaction.

    Reuses the same loader as the lookup endpoint, then hands the records to
    ``investigate``. The rules and evidence live in the service; this route
    only wires the two together.
    """
    records = _load_records_or_404(loader, transaction_id)

    return investigate(transaction_id, records)


@router.get(
    "/{transaction_id}/explanation",
    response_model=ExplanationResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Explain a transaction's investigation result in plain English",
)
async def get_transaction_explanation(
    transaction_id: str,
    loader: DataLoader = Depends(get_data_loader),
    explainer: AIExplanationService = Depends(get_ai_explanation_service),
) -> ExplanationResponse:
    """Investigate the transaction, then have the AI layer explain the result.

    The deterministic engine runs first and its output is returned unchanged in
    ``investigation``. Only that result is passed to the model. If the provider
    is unavailable — or returns something unsupported by the evidence — the
    response falls back to a deterministic summary and says so.
    """
    records = _load_records_or_404(loader, transaction_id)
    investigation = investigate(transaction_id, records)

    return explainer.explain(investigation)
