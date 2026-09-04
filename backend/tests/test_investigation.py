"""Tests for the investigation endpoint and the deterministic engine.

Expected outcomes come from the mock CSVs in ``app/data``, so these tests fail
if the data and the rules ever drift apart.
"""

import pytest

from app.core.config import settings
from app.schemas.investigation import SettlementStatus
from app.services.data_loader import DataLoader
from app.services.investigation import investigate

TRANSACTIONS_URL = f"{settings.API_V1_PREFIX}/transactions"


def investigation_url(transaction_id: str) -> str:
    return f"{TRANSACTIONS_URL}/{transaction_id}/investigation"


# Every demo scenario and the status it must resolve to.
DEMO_CASES = [
    ("TXN10001", SettlementStatus.SETTLED),
    ("TXN10002", SettlementStatus.DELAYED),
    ("TXN10003", SettlementStatus.INCOMPLETE),
    ("TXN10004", SettlementStatus.NEEDS_INVESTIGATION),
    ("TXN10005", SettlementStatus.FAILED),
    ("TXN10006", SettlementStatus.INCOMPLETE),
    ("TXN10007", SettlementStatus.NEEDS_INVESTIGATION),
]


@pytest.mark.parametrize("transaction_id, expected_status", DEMO_CASES)
def test_demo_transactions_resolve_to_expected_status(
    client, transaction_id, expected_status
):
    response = client.get(investigation_url(transaction_id))
    assert response.status_code == 200

    body = response.json()
    assert body["transaction_id"] == transaction_id
    assert body["status"] == expected_status.value


def test_unknown_transaction_returns_404(client):
    response = client.get(investigation_url("TXN99999"))
    assert response.status_code == 404
    assert response.json() == {"detail": "Transaction TXN99999 not found"}


def test_settled_transaction_has_no_exceptions(client):
    """TXN10001 agrees across all three systems, so nothing needs follow-up."""
    body = client.get(investigation_url("TXN10001")).json()

    assert body["exceptions"] == []
    assert body["investigation_confidence"] == 100
    assert body["recommended_action"] == (
        "Confirm settlement reference with the support request."
    )


def test_bank_delay_names_the_bank_as_the_cause(client):
    body = client.get(investigation_url("TXN10002")).json()

    assert body["root_cause"] == "Bank-side settlement delay"
    assert body["recommended_action"] == (
        "Verify bank settlement batch and bank reference."
    )
    assert any("Bank status is DELAYED" in line for line in body["evidence"])


def test_gateway_failure_names_the_gateway_as_the_cause(client):
    body = client.get(investigation_url("TXN10005")).json()

    assert body["root_cause"] == "Gateway transaction failed"
    assert body["recommended_action"] == (
        "Review gateway failure reason and retry/reconciliation status."
    )
    assert any("Gateway status is FAILED" in line for line in body["evidence"])


def test_missing_ledger_is_identified_explicitly(client):
    body = client.get(investigation_url("TXN10003")).json()

    assert body["root_cause"] == "Ledger record missing"
    assert body["exceptions"] == ["Ledger record was not found"]


def test_missing_bank_is_identified_explicitly(client):
    body = client.get(investigation_url("TXN10006")).json()

    assert body["root_cause"] == "Bank record missing"
    assert body["exceptions"] == ["Bank record was not found"]


def test_amount_mismatch_reports_the_differing_systems(client):
    """TXN10004's exception must name the systems and their actual amounts."""
    body = client.get(investigation_url("TXN10004")).json()

    assert body["root_cause"] == "Amount mismatch between systems"
    assert len(body["exceptions"]) == 1

    exception = body["exceptions"][0]
    assert "gateway (7500.00)" in exception
    assert "bank (7000.00)" in exception


def test_multiple_inconsistencies_report_every_problem(client):
    """TXN10007 has three problems; none may be silently dropped."""
    body = client.get(investigation_url("TXN10007")).json()

    assert body["status"] == "NEEDS_INVESTIGATION"

    # The root cause enumerates all of them rather than picking one.
    assert "ledger record missing" in body["root_cause"]
    assert "amount mismatch" in body["root_cause"]
    assert "bank-side settlement delay" in body["root_cause"]

    exceptions = " | ".join(body["exceptions"])
    assert "Ledger record was not found" in exceptions
    assert "Amount mismatch" in exceptions
    assert "delayed" in exceptions


# --- Evidence honesty --------------------------------------------------------


@pytest.mark.parametrize("transaction_id, _expected_status", DEMO_CASES)
def test_evidence_matches_the_looked_up_records(
    client, transaction_id, _expected_status
):
    """Every status quoted as evidence must match the record it came from.

    This is the guard against invented evidence: the investigation is compared
    against the lookup endpoint, which reads the same CSVs.
    """
    lookup = client.get(f"{TRANSACTIONS_URL}/{transaction_id}").json()
    evidence = client.get(investigation_url(transaction_id)).json()["evidence"]

    for source, label in (
        ("gateway", "Gateway"),
        ("bank", "Bank"),
        ("ledger", "Ledger"),
    ):
        record = lookup[source]
        if record is None:
            # An absent system is reported as absent, never given a status.
            assert f"No {source} record was found" in " | ".join(evidence)
            assert f"{label} status is" not in " | ".join(evidence)
        else:
            assert f"{label} status is {record['status']}" in evidence


def test_missing_systems_produce_no_status_evidence(client):
    """TXN10003 has no ledger row, so no ledger status may be claimed."""
    body = client.get(investigation_url("TXN10003")).json()

    assert "No ledger record was found for TXN10003" in body["evidence"]
    assert not any("Ledger status is" in line for line in body["evidence"])


def test_confidence_drops_when_evidence_is_incomplete(client):
    """Confidence reflects evidence quality: complete beats missing records."""
    settled = client.get(investigation_url("TXN10001")).json()
    one_missing = client.get(investigation_url("TXN10003")).json()
    several_problems = client.get(investigation_url("TXN10007")).json()

    assert settled["investigation_confidence"] == 100
    assert (
        settled["investigation_confidence"]
        > one_missing["investigation_confidence"]
        > several_problems["investigation_confidence"]
    )

    for body in (settled, one_missing, several_problems):
        assert 0 <= body["investigation_confidence"] <= 100


# --- Determinism -------------------------------------------------------------


@pytest.mark.parametrize("transaction_id, _expected_status", DEMO_CASES)
def test_repeated_requests_return_identical_results(
    client, transaction_id, _expected_status
):
    """The same transaction must investigate identically every time."""
    results = [
        client.get(investigation_url(transaction_id)).json() for _ in range(5)
    ]

    assert all(result == results[0] for result in results)


def test_engine_is_deterministic_across_fresh_loaders():
    """A brand-new loader and engine call reproduces the same result exactly."""
    first_loader = DataLoader()
    second_loader = DataLoader()

    for transaction_id, _ in DEMO_CASES:
        first = investigate(
            transaction_id, first_loader.find_transaction(transaction_id)
        )
        second = investigate(
            transaction_id, second_loader.find_transaction(transaction_id)
        )
        assert first.model_dump() == second.model_dump()


def test_engine_reports_no_evidence_honestly():
    """With nothing to go on, the engine refuses to name a root cause."""
    empty = {"gateway": None, "bank": None, "ledger": None}

    result = investigate("TXN00000", empty)

    assert result.investigation_confidence == 0
    assert result.root_cause == (
        "Root cause cannot be confirmed from available evidence."
    )
