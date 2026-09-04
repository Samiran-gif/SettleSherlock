"""Framework-agnostic entry point for the SettleSherlock backend.

The backend team can call :func:`run_investigation` from FastAPI, Flask, a
queue worker or a script without importing anything from this package's
internals and without any web framework being a dependency here.

Example (FastAPI)::

    from agent.service import run_investigation

    @app.post("/investigations")
    def create_investigation(payload: dict) -> dict:
        return run_investigation(payload)
"""

from __future__ import annotations

from typing import Any, Mapping

from .config import InvestigationConfig
from .investigator import Investigator
from .llm import LLMProvider

__all__ = ["run_investigation", "health"]


def _config_from_options(options: Mapping[str, Any] | None) -> InvestigationConfig:
    """Build a config from a JSON-ish options mapping, ignoring unknown keys."""
    config = InvestigationConfig.from_env()
    if not options:
        return config
    for key, value in options.items():
        if hasattr(config, key) and value is not None:
            try:
                setattr(config, key, value)
            except (TypeError, ValueError):
                # An unusable override should not fail the request.
                continue
    return config


def run_investigation(
    payload: Any,
    options: Mapping[str, Any] | None = None,
    llm_provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Investigate ``payload`` and return the report as a JSON-safe dict.

    ``payload`` accepts a list of transaction records, a dict wrapping them
    under ``transactions``/``records``/``evidence``/``data``/``items``, or a
    single transaction record. ``options`` may override any
    :class:`~agent.config.InvestigationConfig` field.

    Invalid input never raises: it comes back as a low-confidence report whose
    ``data_quality`` section explains what was wrong.
    """
    if isinstance(payload, Mapping) and options is None:
        # Allow {"transactions": [...], "options": {...}} in a single body.
        nested = payload.get("options")
        if isinstance(nested, Mapping):
            options = nested

    investigator = Investigator(
        config=_config_from_options(options), llm_provider=llm_provider
    )
    return investigator.investigate(payload).to_dict()


def health() -> dict[str, Any]:
    """Readiness information for the backend's health endpoint."""
    from .investigator import AGENT_VERSION
    from .patterns import DETECTORS

    config = InvestigationConfig.from_env()
    investigator = Investigator(config=config)
    return {
        "status": "ok",
        "agent_version": AGENT_VERSION,
        "detector_count": len(DETECTORS),
        "llm_enrichment_enabled": investigator.enricher.enabled,
        "llm_provider": getattr(investigator.enricher.provider, "name", "none"),
    }
