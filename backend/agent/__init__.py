"""SettleSherlock Investigation AI — root-cause analysis for suspicious transactions.

Typical use::

    from agent import investigate

    report = investigate(transactions)     # list of dicts
    print(report.severity, report.root_cause.conclusion)
    payload = report.to_dict()             # JSON-safe contract for the API

Design contract: every number, pattern, evidence link and confidence score is
computed deterministically. An LLM, if configured, may only add prose, and
everything it writes is tagged ``provenance: "llm"``.
"""

from __future__ import annotations

from .analyzer import TransactionAnalysis, analyze_transactions
from .config import InvestigationConfig
from .evidence import score_confidence
from .investigator import AGENT_VERSION, Investigator, investigate
from .llm import AnthropicProvider, LLMEnricher, LLMProvider, NullProvider
from .models import (
    DataQuality,
    DetectedPattern,
    Entity,
    EvidenceGrade,
    EvidenceItem,
    Hypothesis,
    Importance,
    InvestigationReport,
    Provenance,
    Severity,
    TimelineEvent,
    Transaction,
    TransactionStatus,
)
from .patterns import detect_patterns
from .relationships import EntityGraph, build_graph
from .root_cause import RULES, analyze_root_cause
from .service import run_investigation
from .timeline import build_timeline
from .validation import normalize_records

__version__ = AGENT_VERSION

__all__ = [
    "AGENT_VERSION",
    "AnthropicProvider",
    "DataQuality",
    "DetectedPattern",
    "Entity",
    "EntityGraph",
    "EvidenceGrade",
    "EvidenceItem",
    "Hypothesis",
    "Importance",
    "InvestigationConfig",
    "InvestigationReport",
    "Investigator",
    "LLMEnricher",
    "LLMProvider",
    "NullProvider",
    "Provenance",
    "RULES",
    "Severity",
    "TimelineEvent",
    "Transaction",
    "TransactionAnalysis",
    "TransactionStatus",
    "__version__",
    "analyze_root_cause",
    "analyze_transactions",
    "build_graph",
    "build_timeline",
    "detect_patterns",
    "investigate",
    "normalize_records",
    "run_investigation",
    "score_confidence",
]
