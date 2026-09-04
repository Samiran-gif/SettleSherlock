"""The orchestrator: runs the full investigation pipeline.

    validate → analyze → relate → detect → timeline → reason → link → score

Every stage is deterministic. The optional LLM stage runs last and can only
add prose (see :mod:`agent.llm`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence

from .analyzer import TransactionAnalysis, analyze_transactions
from .config import InvestigationConfig
from .evidence import (
    build_evidence,
    collect_confirmed_facts,
    collect_unknowns,
    overall_severity,
    select_suspicious_transactions,
)
from .llm import LLMEnricher, LLMProvider, NullProvider, provider_from_env
from .models import (
    DataQuality,
    DetectedPattern,
    InvestigationReport,
    Provenance,
    Severity,
    Transaction,
)
from .patterns import detect_patterns
from .relationships import EntityGraph, build_graph, summarize_entities
from .root_cause import analyze_root_cause
from .timeline import build_timeline
from .validation import normalize_records

__all__ = ["Investigator", "InvestigationInputs", "investigate"]

#: Bumped when the report contract or scoring changes.
AGENT_VERSION = "1.0.0"


@dataclass(slots=True)
class InvestigationInputs:
    """Intermediate artefacts of a run, exposed for testing and debugging."""

    transactions: list[Transaction]
    quality: DataQuality
    analysis: TransactionAnalysis
    graph: EntityGraph
    patterns: list[DetectedPattern]


class Investigator:
    """Runs root-cause investigations over transaction evidence.

    ``Investigator`` is stateless between calls, so a single instance can be
    reused by a web request handler.
    """

    def __init__(
        self,
        config: InvestigationConfig | None = None,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self.config = config or InvestigationConfig()
        # An explicitly injected provider wins; otherwise honour the config /
        # environment. Tests inject a fake provider and never touch the network.
        if llm_provider is not None:
            provider: LLMProvider = llm_provider
        elif self.config.enable_llm:
            provider = provider_from_env(default_enabled=True)
        else:
            provider = NullProvider(reason="enable_llm is False")
        self.enricher = LLMEnricher(provider=provider)

    # -- pipeline stages -------------------------------------------------- #
    def prepare(self, evidence: Any) -> InvestigationInputs:
        """Run the deterministic stages and return their raw artefacts."""
        transactions, quality = normalize_records(evidence)
        analysis = analyze_transactions(transactions)
        graph = build_graph(transactions)
        patterns = detect_patterns(transactions, analysis, graph, self.config)
        return InvestigationInputs(
            transactions=transactions,
            quality=quality,
            analysis=analysis,
            graph=graph,
            patterns=patterns,
        )

    def investigate(self, evidence: Any) -> InvestigationReport:
        """Investigate an evidence payload and return a structured report.

        ``evidence`` may be a list of transaction dicts, a dict wrapping such a
        list, or a single transaction dict. Malformed records are reported in
        ``data_quality`` rather than raising.
        """
        started = time.perf_counter()
        inputs = self.prepare(evidence)

        patterns = inputs.patterns
        analysis = inputs.analysis
        quality = inputs.quality

        severity = overall_severity(patterns, analysis)
        suspicious = select_suspicious_transactions(inputs.transactions, patterns)
        timeline = build_timeline(inputs.transactions, patterns, self.config)
        evidence_items = build_evidence(
            inputs.transactions, patterns, quality, self.config
        )
        root_cause_result = analyze_root_cause(
            inputs.transactions, analysis, patterns, quality, self.config
        )
        flagged_entities = {e for p in patterns for e in p.entities}
        entities = summarize_entities(inputs.transactions, inputs.graph, flagged_entities)
        facts = collect_confirmed_facts(analysis, patterns, quality)
        unknowns = collect_unknowns(inputs.transactions, analysis, quality, patterns)

        report = InvestigationReport(
            case_summary=self._summarize(
                severity, analysis, patterns, root_cause_result.primary.conclusion, quality
            ),
            severity=severity,
            suspicious_transactions=suspicious,
            entities_involved=entities,
            timeline=timeline,
            detected_patterns=patterns,
            root_cause=root_cause_result.primary,
            evidence=evidence_items,
            alternative_hypotheses=root_cause_result.alternatives,
            unknowns=unknowns,
            recommended_next_steps=root_cause_result.next_steps,
            confirmed_facts=facts,
            statistics=analysis.to_dict(),
            data_quality=quality,
            case_summary_source=Provenance.DETERMINISTIC,
        )

        # Optional, additive, and incapable of changing anything above.
        narrative = self.enricher.enrich(report.to_dict())
        if narrative:
            report.ai_narrative = narrative
            summary = narrative.get("case_summary")
            if isinstance(summary, str) and summary.strip():
                report.meta["deterministic_case_summary"] = report.case_summary
                report.case_summary = summary.strip()
                report.case_summary_source = Provenance.LLM
            for request in narrative.get("additional_evidence_requests") or []:
                if not isinstance(request, str) or not request.strip():
                    continue
                marked = f"[AI suggestion] {request.strip()}"
                if marked not in report.recommended_next_steps:
                    report.recommended_next_steps.append(marked)

        report.meta.update(
            {
                "agent_version": AGENT_VERSION,
                "analysis": "deterministic",
                "llm_enrichment": "applied" if narrative else "not_applied",
                "llm_provider": getattr(self.enricher.provider, "name", "none"),
                "graph": inputs.graph.to_dict(),
                "config": self.config.to_dict(),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )
        return report

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _summarize(
        severity: Severity,
        analysis: TransactionAnalysis,
        patterns: Sequence[DetectedPattern],
        conclusion: str,
        quality: DataQuality,
    ) -> str:
        """Compose the deterministic case summary (always present)."""
        if quality.records_parsed == 0:
            return (
                "No usable transaction records were submitted, so no investigation "
                "could be performed."
            )

        headline = f"{analysis.transaction_count} transaction record(s) analysed"
        if analysis.amounts.count:
            currencies = "/".join(sorted(analysis.amounts.currencies)) or "unknown currency"
            headline += f", totalling {analysis.amounts.total} {currencies}"
        if analysis.timing.first_event and analysis.timing.last_event:
            headline += (
                f", spanning {analysis.timing.first_event.date().isoformat()} to "
                f"{analysis.timing.last_event.date().isoformat()}"
            )

        if not patterns:
            return (
                f"{headline}. No suspicious pattern was detected. "
                f"Case severity: {severity.value}."
            )

        top = [p.name for p in patterns[:3]]
        return (
            f"{headline}. {len(patterns)} pattern(s) detected — most significant: "
            f"{'; '.join(top)}. Working conclusion: {conclusion} "
            f"Case severity: {severity.value}."
        )


def investigate(
    evidence: Any,
    config: InvestigationConfig | None = None,
    llm_provider: LLMProvider | None = None,
) -> InvestigationReport:
    """Convenience wrapper around :class:`Investigator` for one-off use."""
    return Investigator(config=config, llm_provider=llm_provider).investigate(evidence)
