"""Optional LLM layer: interpretation and prose only.

The investigation is complete and fully scored before this module runs. An LLM
is used strictly to *explain* what the deterministic pipeline already found:

* it receives a digest of the computed findings, never the raw ledger, and is
  told not to invent transactions, amounts or confidences;
* only a whitelist of keys is copied out of its response, so it cannot alter
  severity, confidence, patterns or evidence links;
* everything it produces is tagged ``provenance: "llm"`` in the report.

Configuration is entirely through environment variables — no credential is
ever read from or written to the repository. See ``.env.example``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

__all__ = [
    "LLMProvider",
    "NullProvider",
    "AnthropicProvider",
    "LLMEnricher",
    "provider_from_env",
    "NARRATIVE_KEYS",
    "DEFAULT_MODEL",
]

logger = logging.getLogger(__name__)

#: Default model for narrative generation. Overridable with
#: ``SETTLESHERLOCK_LLM_MODEL``.
DEFAULT_MODEL = "claude-opus-5"

#: The only keys copied out of an LLM response into the report.
NARRATIVE_KEYS: tuple[str, ...] = (
    "case_summary",
    "narrative",
    "pattern_interpretation",
    "hypothesis_assessment",
    "additional_evidence_requests",
)

_SYSTEM_PROMPT = """\
You are the narrative layer of SettleSherlock, a financial-transaction \
investigation system. A deterministic analysis engine has already validated the \
records, detected the patterns, linked the evidence and calculated every \
confidence score. Your only job is to explain those findings to a human \
investigator.

Hard rules:
1. Use ONLY the facts in the digest you are given. Never invent a transaction \
id, amount, entity, date or count.
2. Never contradict, re-rank or re-score the engine's severity, confidence or \
patterns. If you believe a conclusion is weak, say so in \
`hypothesis_assessment` rather than changing anything.
3. Cite transaction ids when you refer to specific records.
4. Distinguish what is established from what is inferred, and name what is \
still unknown.
5. Be concise and neutral. No speculation about individuals' intent beyond what \
the evidence supports, and no legal conclusions.
"""

#: JSON schema for the narrative response (structured outputs).
_NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "case_summary": {
            "type": "string",
            "description": "Two to four sentences summarising what happened.",
        },
        "narrative": {
            "type": "string",
            "description": "A chronological plain-language account of the case.",
        },
        "pattern_interpretation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pattern_id": {"type": "string"},
                    "interpretation": {"type": "string"},
                },
                "required": ["pattern_id", "interpretation"],
                "additionalProperties": False,
            },
            "description": "What each detected pattern means in practice.",
        },
        "hypothesis_assessment": {
            "type": "string",
            "description": (
                "Comparison of the leading explanation against the alternatives, "
                "including why the alternatives are less likely."
            ),
        },
        "additional_evidence_requests": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Further evidence that would resolve the open questions.",
        },
    },
    "required": [
        "case_summary",
        "narrative",
        "pattern_interpretation",
        "hypothesis_assessment",
        "additional_evidence_requests",
    ],
    "additionalProperties": False,
}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _load_dotenv_if_available() -> None:
    """Best-effort ``.env`` loading; a no-op if python-dotenv is absent."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        load_dotenv(override=False)
    except Exception:  # pragma: no cover - never fail because of config loading
        logger.debug("dotenv loading failed", exc_info=True)


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #
@runtime_checkable
class LLMProvider(Protocol):
    """Minimal provider contract: JSON in, JSON out, never raises."""

    name: str

    def complete_json(
        self, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Return a parsed JSON object, or ``None`` if generation failed."""
        ...


@dataclass(slots=True)
class NullProvider:
    """The default: no LLM configured, so no narrative is produced."""

    name: str = "none"
    reason: str = "LLM enrichment is disabled"

    def complete_json(
        self, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        return None


@dataclass(slots=True)
class AnthropicProvider:
    """Anthropic Claude provider using structured JSON output.

    The ``anthropic`` package is imported lazily so the core agent — and the
    test suite — never require it.
    """

    model: str = DEFAULT_MODEL
    max_tokens: int = 8000
    timeout: float = 60.0
    #: When set, a policy refusal is retried server-side on this model.
    fallback_model: str | None = None
    api_key: str | None = None
    name: str = field(default="anthropic", init=False)

    def _client(self) -> Any:
        import anthropic  # imported here so the dependency stays optional

        # Passing api_key=None lets the SDK resolve credentials itself
        # (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile).
        return anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)

    def complete_json(
        self, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": {"type": "json_schema", "schema": dict(schema)}},
        }

        try:
            client = self._client()
            if self.fallback_model:
                response = client.beta.messages.create(
                    betas=["server-side-fallback-2026-06-01"],
                    fallbacks=[{"model": self.fallback_model}],
                    **request,
                )
            else:
                response = client.messages.create(**request)
        except ImportError:
            logger.warning(
                "LLM enrichment requested but the 'anthropic' package is not "
                "installed; continuing with the deterministic report only."
            )
            return None
        except Exception as exc:
            # A failed narrative must never fail an investigation.
            logger.warning("LLM call failed (%s); continuing without narrative.", exc)
            return None

        if getattr(response, "stop_reason", None) == "refusal":
            logger.warning("LLM declined to generate a narrative for this case.")
            return None

        text = next(
            (
                block.text
                for block in getattr(response, "content", [])
                if getattr(block, "type", None) == "text"
            ),
            None,
        )
        if not text:
            return None
        return _parse_json_object(text)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object out of a model response, tolerating code fences."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def provider_from_env(default_enabled: bool = False) -> LLMProvider:
    """Build a provider from the environment.

    Returns :class:`NullProvider` unless enrichment is enabled — by
    ``SETTLESHERLOCK_LLM_ENABLED`` or by ``default_enabled`` (used when the
    caller already opted in through config or the ``--llm`` flag) — and a
    supported provider is selected. An explicit ``SETTLESHERLOCK_LLM_ENABLED``
    of ``false`` always wins, so an operator can switch the feature off without
    touching code.
    """
    _load_dotenv_if_available()

    if not _env_flag("SETTLESHERLOCK_LLM_ENABLED", default_enabled):
        return NullProvider(reason="LLM enrichment is not enabled")

    provider_name = (os.getenv("SETTLESHERLOCK_LLM_PROVIDER") or "anthropic").lower()
    if provider_name in ("none", "null", "off"):
        return NullProvider(reason="SETTLESHERLOCK_LLM_PROVIDER=none")
    if provider_name != "anthropic":
        return NullProvider(reason=f"unsupported LLM provider {provider_name!r}")

    def _int_env(name: str, default: int) -> int:
        try:
            return int(os.getenv(name) or default)
        except ValueError:
            return default

    def _float_env(name: str, default: float) -> float:
        try:
            return float(os.getenv(name) or default)
        except ValueError:
            return default

    return AnthropicProvider(
        model=os.getenv("SETTLESHERLOCK_LLM_MODEL") or DEFAULT_MODEL,
        max_tokens=_int_env("SETTLESHERLOCK_LLM_MAX_TOKENS", 8000),
        timeout=_float_env("SETTLESHERLOCK_LLM_TIMEOUT_SECONDS", 60.0),
        fallback_model=os.getenv("SETTLESHERLOCK_LLM_FALLBACK_MODEL") or None,
        # Left as None on purpose: the SDK resolves the credential itself.
        api_key=None,
    )


# --------------------------------------------------------------------------- #
# Enrichment
# --------------------------------------------------------------------------- #
def _digest(report: Mapping[str, Any]) -> str:
    """Build the compact, fact-only digest handed to the model."""
    root_cause = report.get("root_cause") or {}
    digest = {
        "deterministic_case_summary": report.get("case_summary"),
        "severity": report.get("severity"),
        "statistics": report.get("statistics"),
        "data_quality": {
            key: (report.get("data_quality") or {}).get(key)
            for key in (
                "records_submitted",
                "records_parsed",
                "records_rejected",
                "field_completeness",
                "conflicting_record_count",
                "missing_fields",
            )
        },
        "confirmed_facts": report.get("confirmed_facts"),
        "detected_patterns": [
            {
                "pattern_id": p.get("pattern_id"),
                "name": p.get("name"),
                "description": p.get("description"),
                "severity": p.get("severity"),
                "confidence": p.get("confidence"),
                "transaction_ids": p.get("transaction_ids"),
            }
            for p in report.get("detected_patterns") or []
        ],
        "suspicious_transactions": report.get("suspicious_transactions"),
        "timeline": [
            {
                "timestamp": e.get("timestamp"),
                "transaction_id": e.get("transaction_id"),
                "event": e.get("event"),
                "importance": e.get("importance"),
            }
            for e in report.get("timeline") or []
        ],
        "root_cause": {
            "conclusion": root_cause.get("conclusion"),
            "confidence": root_cause.get("confidence"),
            "reasoning": root_cause.get("reasoning"),
        },
        "alternative_hypotheses": [
            {"conclusion": h.get("conclusion"), "confidence": h.get("confidence")}
            for h in report.get("alternative_hypotheses") or []
        ],
        "unknowns": report.get("unknowns"),
        "recommended_next_steps": report.get("recommended_next_steps"),
    }
    return json.dumps(digest, indent=2, default=str)


@dataclass(slots=True)
class LLMEnricher:
    """Turns a deterministic report into human-readable prose, safely."""

    provider: LLMProvider

    @property
    def enabled(self) -> bool:
        return not isinstance(self.provider, NullProvider)

    def enrich(self, report: Mapping[str, Any]) -> dict[str, Any] | None:
        """Return the whitelisted narrative fields, or ``None`` on any failure."""
        if not self.enabled:
            return None

        prompt = (
            "Here is the completed deterministic analysis of a suspicious-transaction "
            "case. Explain it for the investigator who will action it.\n\n"
            f"{_digest(report)}"
        )

        try:
            raw = self.provider.complete_json(_SYSTEM_PROMPT, prompt, _NARRATIVE_SCHEMA)
        except Exception as exc:
            # The narrative is a nice-to-have; a broken provider must not cost
            # the investigator their report.
            logger.warning(
                "LLM provider %r raised %s; returning the deterministic report only.",
                getattr(self.provider, "name", "unknown"),
                exc,
            )
            return None

        if not isinstance(raw, dict):
            return None

        narrative: dict[str, Any] = {
            key: raw[key] for key in NARRATIVE_KEYS if key in raw
        }
        if not narrative:
            return None

        narrative["provenance"] = "llm"
        narrative["model"] = getattr(self.provider, "model", None)
        narrative["provider"] = getattr(self.provider, "name", "unknown")
        narrative["disclaimer"] = (
            "AI-generated interpretation of the deterministic findings. It does not "
            "alter any severity, confidence score, pattern or evidence link."
        )
        return narrative
