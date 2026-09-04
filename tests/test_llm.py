"""Tests for the optional LLM layer.

These tests never touch the network and never need an API key: they inject a
fake provider and assert the safety boundary around it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import pytest

from agent import investigate
from agent.config import InvestigationConfig
from agent.llm import (
    LLMEnricher,
    NullProvider,
    _parse_json_object,
    provider_from_env,
)
from agent.models import Provenance


@dataclass
class FakeProvider:
    """Records what it was asked and returns a canned narrative."""

    response: dict[str, Any] | None
    name: str = "fake"
    model: str = "fake-model"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete_json(
        self, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        self.calls.append((system, prompt))
        return self.response


GOOD_RESPONSE = {
    "case_summary": "One order was charged twice within 41 seconds.",
    "narrative": "TXN-1001 failed, then TXN-1002 and TXN-1003 both settled.",
    "pattern_interpretation": [
        {"pattern_id": "duplicate_settlement_reference", "interpretation": "Double charge."}
    ],
    "hypothesis_assessment": "The retry explanation fits better than fraud.",
    "additional_evidence_requests": ["Pull the PayFlow request log for ORD-88231."],
}


class TestDefaults:
    def test_provider_is_null_without_configuration(self, monkeypatch):
        monkeypatch.delenv("SETTLESHERLOCK_LLM_ENABLED", raising=False)
        assert isinstance(provider_from_env(), NullProvider)

    def test_provider_is_null_when_explicitly_disabled(self, monkeypatch):
        monkeypatch.setenv("SETTLESHERLOCK_LLM_ENABLED", "true")
        monkeypatch.setenv("SETTLESHERLOCK_LLM_PROVIDER", "none")
        assert isinstance(provider_from_env(), NullProvider)

    def test_unsupported_provider_degrades_instead_of_raising(self, monkeypatch):
        monkeypatch.setenv("SETTLESHERLOCK_LLM_ENABLED", "1")
        monkeypatch.setenv("SETTLESHERLOCK_LLM_PROVIDER", "some-other-vendor")
        provider = provider_from_env()
        assert isinstance(provider, NullProvider)
        assert "unsupported" in provider.reason

    def test_null_provider_produces_no_narrative(self):
        enricher = LLMEnricher(provider=NullProvider())
        assert enricher.enabled is False
        assert enricher.enrich({"case_summary": "x"}) is None


class TestEnrichment:
    def test_narrative_is_attached_and_tagged(self, duplicate_case):
        provider = FakeProvider(response=GOOD_RESPONSE)
        report = investigate(
            duplicate_case,
            config=InvestigationConfig(enable_llm=True),
            llm_provider=provider,
        )
        assert report.ai_narrative is not None
        assert report.ai_narrative["provenance"] == "llm"
        assert report.ai_narrative["model"] == "fake-model"
        assert report.case_summary == GOOD_RESPONSE["case_summary"]
        assert report.case_summary_source is Provenance.LLM
        assert report.meta["llm_enrichment"] == "applied"

    def test_deterministic_summary_is_preserved(self, duplicate_case):
        report = investigate(
            duplicate_case,
            config=InvestigationConfig(enable_llm=True),
            llm_provider=FakeProvider(response=GOOD_RESPONSE),
        )
        preserved = report.meta["deterministic_case_summary"]
        assert "transaction record(s) analysed" in preserved

    def test_ai_suggestions_are_marked(self, duplicate_case):
        report = investigate(
            duplicate_case,
            config=InvestigationConfig(enable_llm=True),
            llm_provider=FakeProvider(response=GOOD_RESPONSE),
        )
        assert any(
            step.startswith("[AI suggestion]") for step in report.recommended_next_steps
        )
        assert any(
            not step.startswith("[AI suggestion]") for step in report.recommended_next_steps
        )

    def test_the_model_cannot_change_findings(self, duplicate_case):
        hostile = dict(GOOD_RESPONSE)
        hostile.update(
            {
                "severity": "low",
                "detected_patterns": [],
                "root_cause": {"conclusion": "nothing happened", "confidence": 0.01},
                "suspicious_transactions": [],
                "evidence": [],
            }
        )
        baseline = investigate(duplicate_case)
        enriched = investigate(
            duplicate_case,
            config=InvestigationConfig(enable_llm=True),
            llm_provider=FakeProvider(response=hostile),
        )
        assert enriched.severity is baseline.severity
        assert enriched.root_cause.conclusion == baseline.root_cause.conclusion
        assert enriched.root_cause.confidence == baseline.root_cause.confidence
        assert enriched.pattern_ids() == baseline.pattern_ids()
        assert enriched.suspicious_transactions == baseline.suspicious_transactions
        # Nothing outside the whitelist leaks into the report.
        assert "detected_patterns" not in enriched.ai_narrative

    def test_provider_receives_findings_not_raw_records(self, duplicate_case):
        provider = FakeProvider(response=GOOD_RESPONSE)
        investigate(
            duplicate_case,
            config=InvestigationConfig(enable_llm=True),
            llm_provider=provider,
        )
        system, prompt = provider.calls[0]
        assert "Never invent" in system
        assert "duplicate_settlement_reference" in prompt
        assert "gateway_code" not in prompt  # raw metadata is not forwarded

    @pytest.mark.parametrize("response", [None, {}, {"unrelated": "value"}, "not-a-dict"])
    def test_bad_responses_degrade_silently(self, duplicate_case, response):
        report = investigate(
            duplicate_case,
            config=InvestigationConfig(enable_llm=True),
            llm_provider=FakeProvider(response=response),
        )
        assert report.ai_narrative is None
        assert report.case_summary_source is Provenance.DETERMINISTIC
        assert report.meta["llm_enrichment"] == "not_applied"
        assert report.has_pattern("duplicate_settlement_reference")

    def test_provider_exceptions_never_break_an_investigation(self, duplicate_case):
        class ExplodingProvider:
            name = "exploding"

            def complete_json(self, system, prompt, schema):
                raise RuntimeError("upstream is down")

        report = investigate(
            duplicate_case,
            config=InvestigationConfig(enable_llm=True),
            llm_provider=ExplodingProvider(),
        )
        assert report.ai_narrative is None
        assert report.has_pattern("duplicate_settlement_reference")
        assert report.meta["llm_enrichment"] == "not_applied"


class TestJsonParsing:
    def test_plain_object(self):
        assert _parse_json_object('{"a": 1}') == {"a": 1}

    def test_fenced_object(self):
        assert _parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_object_with_surrounding_prose(self):
        assert _parse_json_object('Sure!\n{"a": 1}\nHope that helps.') == {"a": 1}

    def test_non_object_and_garbage(self):
        assert _parse_json_object("[1, 2, 3]") is None
        assert _parse_json_object("not json at all") is None


class TestAnthropicProviderIsLazy:
    def test_importing_the_agent_does_not_require_the_sdk(self):
        import sys

        assert "anthropic" not in sys.modules

    def test_missing_sdk_returns_none_rather_than_raising(self, monkeypatch):
        from agent.llm import AnthropicProvider

        provider = AnthropicProvider(model="claude-opus-5")
        monkeypatch.setattr(
            provider.__class__,
            "_client",
            lambda self: (_ for _ in ()).throw(ImportError("no anthropic")),
        )
        assert provider.complete_json("s", "p", {}) is None
