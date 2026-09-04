"""Tunable thresholds for the investigation pipeline.

Every threshold that influences a detection lives here so that a case can be
re-run with different sensitivity, and so the report can record exactly which
settings produced it (see ``InvestigationReport.meta``).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

__all__ = ["InvestigationConfig"]


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment flag ("1", "true", "yes", "on")."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(slots=True)
class InvestigationConfig:
    """Detection thresholds and feature switches."""

    # -- duplicates ------------------------------------------------------- #
    #: Two value-moving transfers with identical party/amount/currency inside
    #: this window are treated as a near-duplicate pair.
    duplicate_window_seconds: float = 300.0
    #: Minimum repeats of an identical amount between the same parties before
    #: it is reported as a repeated-transaction pattern.
    repeat_min_count: int = 3

    # -- bursts ----------------------------------------------------------- #
    #: Bursts are evaluated per sending account over this window. Five minutes
    #: is wide enough to catch a scripted drain (transfers seconds to tens of
    #: seconds apart) without firing on ordinary back-to-back activity.
    burst_window_seconds: float = 300.0
    burst_min_count: int = 4
    #: A burst must also exceed this multiple of the typical window density.
    burst_density_multiple: float = 3.0

    # -- amounts ---------------------------------------------------------- #
    amount_outlier_z: float = 3.5
    amount_outlier_min_sample: int = 5
    #: Amounts this many times above the median are always flagged.
    amount_ratio_threshold: float = 10.0

    # -- retries / state -------------------------------------------------- #
    retry_window_seconds: float = 3600.0

    # -- graph ------------------------------------------------------------ #
    chain_window_seconds: float = 3600.0
    #: Consecutive hops must retain at least this fraction of the amount to be
    #: considered a pass-through chain.
    chain_amount_tolerance: float = 0.20
    fan_min_counterparties: int = 4
    fan_window_seconds: float = 900.0
    max_cycle_length: int = 5

    # -- structuring ------------------------------------------------------ #
    structuring_min_count: int = 3
    #: How far below a round reporting threshold counts as "just under".
    structuring_band: float = 0.15

    # -- misc ------------------------------------------------------------- #
    off_hours_start: int = 0
    off_hours_end: int = 5
    off_hours_min_count: int = 3
    off_hours_min_ratio: float = 0.6
    gateway_failure_min_count: int = 3
    gateway_failure_min_rate: float = 0.5

    # -- report shaping --------------------------------------------------- #
    max_evidence_items: int = 60
    max_timeline_events: int = 200
    max_alternative_hypotheses: int = 3

    # -- LLM -------------------------------------------------------------- #
    #: LLM enrichment is opt-in; the deterministic report is always complete
    #: without it.
    enable_llm: bool = False

    @classmethod
    def from_env(cls) -> "InvestigationConfig":
        """Build a config, reading only the LLM switch from the environment."""
        return cls(enable_llm=_env_flag("SETTLESHERLOCK_LLM_ENABLED", False))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
