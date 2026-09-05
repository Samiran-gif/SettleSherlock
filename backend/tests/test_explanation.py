"""Tests for the AI explanation layer.

No test contacts a real provider. ``httpx.MockTransport`` stands in for one, so
the service's real request building, response parsing, guard rails and fallback
paths are all exercised against controlled responses.
"""

import httpx
import pytest

from app.core.config import Settings
from app.main import app
from app.schemas.investigation import SettlementStatus
from app.services.ai_explanation import (
    AIExplanationService,
    get_ai_explanation_service,
)
from app.services.data_loader import DataLoader
from app.services.investigation import investigate

EXPLANATION_URL = "/api/v1/transactions/{}/explanation"

# A reply that only restates facts the engine produced.
GROUNDED_REPLY = (
    "This transaction is delayed on the bank side. The gateway authorised it "
    "and the ledger recorded it, but the bank leg is still marked DELAYED. "
    "Verify the bank settlement batch and bank reference."
)


def ai_settings(**overrides) -> Settings:
    """Settings with the AI layer switched on and a dummy key."""
    values = {
        "AI_ENABLED": True,
        "AI_API_KEY": "test-key-not-a-real-secret",
        "AI_BASE_URL": "https://provider.test/openai/v1",
        "AI_MODEL": "test-model",
    }
    values.update(overrides)
    return Settings(**values)


def completion(text: str) -> dict:
    """An OpenAI-compatible chat-completions body."""
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def service_returning(text: str, **setting_overrides) -> AIExplanationService:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(text))

    return AIExplanationService(
        settings=ai_settings(**setting_overrides),
        transport=httpx.MockTransport(handler),
    )


def service_failing(exc_or_status, **setting_overrides) -> AIExplanationService:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(exc_or_status, int):
            return httpx.Response(exc_or_status, json={"error": "nope"})
        raise exc_or_status

    return AIExplanationService(
        settings=ai_settings(**setting_overrides),
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def investigation():
    """The real TXN10002 investigation result, straight from the engine."""
    loader = DataLoader()
    return investigate("TXN10002", loader.find_transaction("TXN10002"))


@pytest.fixture
def client_with_ai():
    """Test client whose explanation service talks to a stubbed provider."""

    def _client(service: AIExplanationService):
        app.dependency_overrides[get_ai_explanation_service] = lambda: service
        return app

    yield _client
    app.dependency_overrides.pop(get_ai_explanation_service, None)


# --- Successful AI response --------------------------------------------------


def test_ai_explanation_is_returned_when_the_provider_succeeds(investigation):
    result = service_returning(GROUNDED_REPLY).explain(investigation)

    assert result.source.value == "ai"
    assert result.ai_available is True
    assert result.model == "test-model"
    assert result.notice is None
    assert result.explanation == GROUNDED_REPLY


def test_endpoint_returns_ai_explanation(client_with_ai):
    from fastapi.testclient import TestClient

    configured_app = client_with_ai(service_returning(GROUNDED_REPLY))
    with TestClient(configured_app) as client:
        response = client.get(EXPLANATION_URL.format("TXN10002"))

    assert response.status_code == 200
    body = response.json()
    assert body["transaction_id"] == "TXN10002"
    assert body["source"] == "ai"
    assert body["ai_available"] is True
    assert body["explanation"] == GROUNDED_REPLY


def test_deterministic_result_is_returned_unmodified(client_with_ai):
    """The engine stays the source of truth: AI must not alter its verdict."""
    from fastapi.testclient import TestClient

    configured_app = client_with_ai(service_returning(GROUNDED_REPLY))
    with TestClient(configured_app) as client:
        explanation = client.get(EXPLANATION_URL.format("TXN10002")).json()
        investigation = client.get(
            "/api/v1/transactions/TXN10002/investigation"
        ).json()

    assert explanation["investigation"] == investigation


def test_only_investigation_facts_are_sent_to_the_provider(investigation):
    """The prompt must not carry records or fields the engine did not produce."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=completion(GROUNDED_REPLY))

    service = AIExplanationService(
        settings=ai_settings(), transport=httpx.MockTransport(handler)
    )
    service.explain(investigation)

    prompt = captured["body"]
    assert investigation.status.value in prompt
    assert investigation.root_cause in prompt
    for item in investigation.evidence:
        assert item in prompt

    # Reference numbers live in the CSV records, not the investigation result,
    # so they must never reach the provider.
    assert "GW10002" not in prompt
    assert "BANK10002" not in prompt
    assert "LEDGER10002" not in prompt

    assert captured["auth"] == "Bearer test-key-not-a-real-secret"


# --- Failure and fallback ----------------------------------------------------


def test_fallback_when_no_api_key_is_configured(investigation):
    service = AIExplanationService(settings=ai_settings(AI_API_KEY=""))

    result = service.explain(investigation)

    assert result.source.value == "fallback"
    assert result.ai_available is False
    assert result.model is None
    assert "AI_API_KEY is not configured" in result.notice


def test_fallback_when_ai_is_disabled(investigation):
    service = AIExplanationService(settings=ai_settings(AI_ENABLED=False))

    result = service.explain(investigation)

    assert result.source.value == "fallback"
    assert "disabled" in result.notice


def test_fallback_on_provider_http_error(investigation):
    result = service_failing(500).explain(investigation)

    assert result.source.value == "fallback"
    assert result.ai_available is False
    assert "HTTP 500" in result.notice


def test_fallback_on_provider_timeout(investigation):
    result = service_failing(httpx.ConnectTimeout("too slow")).explain(investigation)

    assert result.source.value == "fallback"
    assert "provider request failed" in result.notice


def test_fallback_on_unparseable_provider_response(investigation):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    service = AIExplanationService(
        settings=ai_settings(), transport=httpx.MockTransport(handler)
    )
    result = service.explain(investigation)

    assert result.source.value == "fallback"
    assert "not understood" in result.notice


def test_fallback_on_empty_provider_response(investigation):
    result = service_returning("   ").explain(investigation)

    assert result.source.value == "fallback"
    assert "empty" in result.notice


def test_fallback_text_states_ai_is_unavailable_and_carries_the_result(
    investigation,
):
    """Requirement: the fallback must be honest and still be useful."""
    result = service_failing(500).explain(investigation)

    assert "AI explanation is unavailable" in result.explanation
    assert investigation.status.value in result.explanation
    assert investigation.root_cause in result.explanation
    assert investigation.recommended_action in result.explanation

    # The deterministic result is still returned in full.
    assert result.investigation == investigation


def test_endpoint_falls_back_instead_of_erroring(client_with_ai):
    """A dead provider must not turn into a 500 for the caller."""
    from fastapi.testclient import TestClient

    configured_app = client_with_ai(service_failing(503))
    with TestClient(configured_app) as client:
        response = client.get(EXPLANATION_URL.format("TXN10005"))

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "fallback"
    assert body["ai_available"] is False
    assert "AI explanation is unavailable" in body["explanation"]
    assert body["investigation"]["status"] == "FAILED"


# --- Unknown transaction -----------------------------------------------------


def test_unknown_transaction_returns_404(client):
    response = client.get(EXPLANATION_URL.format("TXN99999"))

    assert response.status_code == 404
    assert response.json() == {"detail": "Transaction TXN99999 not found"}


def test_unknown_transaction_never_reaches_the_provider(client_with_ai):
    """A 404 must short-circuit before any provider call is made."""
    from fastapi.testclient import TestClient

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, json=completion(GROUNDED_REPLY))

    service = AIExplanationService(
        settings=ai_settings(), transport=httpx.MockTransport(handler)
    )
    configured_app = client_with_ai(service)
    with TestClient(configured_app) as client:
        response = client.get(EXPLANATION_URL.format("TXN99999"))

    assert response.status_code == 404
    assert calls == []


# --- Hallucination guard -----------------------------------------------------


@pytest.mark.parametrize(
    "invented_reply, invented_value",
    [
        ("The transaction was for 8123.45 and is delayed.", "8123.45"),
        ("Bank reference BANK99999 is still pending.", "99999"),
        ("The bank posted it at 2026-09-14T03:00:00.", "14"),
    ],
)
def test_invented_values_are_rejected(investigation, invented_reply, invented_value):
    """A model that adds amounts, references or timestamps must be discarded."""
    result = service_returning(invented_reply).explain(investigation)

    assert result.source.value == "fallback"
    assert result.ai_available is False
    assert "not in the evidence" in result.notice
    assert invented_value not in result.explanation


def test_grounded_numbers_are_accepted(investigation):
    """Numbers that do appear in the evidence must not trip the guard."""
    reply = (
        "The gateway and ledger agree at 5000.00 but the bank leg is DELAYED, "
        "with confidence 94 out of 100."
    )

    result = service_returning(reply).explain(investigation)

    assert result.source.value == "ai"
    assert result.explanation == reply


def test_currency_formatting_is_not_treated_as_invented(investigation):
    """"$5,000.00" is the evidence's 5000.00, just formatted for a human."""
    reply = "The gateway, bank and ledger all show $5,000.00 for this payment."

    result = service_returning(reply).explain(investigation)

    assert result.source.value == "ai"


def test_unsupported_numbers_helper_is_precise():
    facts = "Settlement status: DELAYED\nAmounts match at 5000.00"

    assert AIExplanationService.unsupported_numbers("shows 5000.00", facts) == []
    assert AIExplanationService.unsupported_numbers("shows 7250.10", facts) == [
        "7250.10"
    ]


def test_ai_cannot_change_the_deterministic_verdict(investigation):
    """Even if the model contradicts the engine, the engine's fields stand."""
    reply = "This transaction settled successfully with no problems at all."

    result = service_returning(reply).explain(investigation)

    assert result.investigation.status == SettlementStatus.DELAYED
    assert result.investigation.root_cause == "Bank-side settlement delay"
    assert result.investigation.investigation_confidence == 94
