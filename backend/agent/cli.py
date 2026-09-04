"""Command-line entry point.

    python -m agent.cli examples/duplicate_charge.json
    python -m agent.cli examples/duplicate_charge.json --summary
    Get-Content case.json | python -m agent.cli -
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import InvestigationConfig
from .investigator import Investigator
from .models import InvestigationReport

__all__ = ["main"]


def _read_payload(source: str) -> Any:
    """Load a JSON payload from a file path or from stdin when ``source`` is ``-``."""
    if source == "-":
        raw = sys.stdin.read()
    else:
        path = Path(source)
        if not path.is_file():
            raise SystemExit(f"error: no such file: {source}")
        raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: {source} is not valid JSON ({exc})") from exc


def _print_summary(report: InvestigationReport) -> None:
    """Human-readable digest for terminal use."""
    print(f"SEVERITY : {report.severity.value.upper()}")
    print(f"SUMMARY  : {report.case_summary}")
    if report.root_cause:
        print(f"ROOT CAUSE ({report.root_cause.confidence:.2f} confidence):")
        print(f"  {report.root_cause.conclusion}")
        for line in report.root_cause.reasoning:
            print(f"  - {line}")
    if report.detected_patterns:
        print("\nPATTERNS:")
        for pattern in report.detected_patterns:
            print(
                f"  [{pattern.severity.value:8}] {pattern.name} "
                f"({pattern.confidence:.2f}) — {pattern.description}"
            )
    if report.suspicious_transactions:
        print("\nSUSPICIOUS TRANSACTIONS:")
        for row in report.suspicious_transactions:
            print(
                f"  {row['transaction_id']:<20} risk={row['risk_score']:<5} "
                f"{', '.join(row['reasons'])}"
            )
    if report.alternative_hypotheses:
        print("\nALTERNATIVE HYPOTHESES:")
        for hypothesis in report.alternative_hypotheses:
            print(f"  ({hypothesis.confidence:.2f}) {hypothesis.conclusion}")
    if report.unknowns:
        print("\nUNKNOWNS:")
        for unknown in report.unknowns:
            print(f"  - {unknown}")
    if report.recommended_next_steps:
        print("\nRECOMMENDED NEXT STEPS:")
        for step in report.recommended_next_steps:
            print(f"  - {step}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent.cli",
        description="Run a SettleSherlock investigation over transaction evidence.",
    )
    parser.add_argument("payload", help="path to a JSON evidence file, or '-' for stdin")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print a human-readable digest instead of the full JSON report",
    )
    parser.add_argument(
        "--out", metavar="PATH", help="write the JSON report to PATH as well as stdout"
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help=(
            "enable optional LLM narrative enrichment (requires provider "
            "environment variables; see .env.example)"
        ),
    )
    parser.add_argument(
        "--compact", action="store_true", help="emit single-line JSON"
    )
    args = parser.parse_args(argv)

    payload = _read_payload(args.payload)
    config = InvestigationConfig.from_env()
    if args.llm:
        config.enable_llm = True

    report = Investigator(config=config).investigate(payload)

    if args.summary:
        _print_summary(report)
    else:
        print(report.to_json(indent=None if args.compact else 2))

    if args.out:
        Path(args.out).write_text(report.to_json(), encoding="utf-8")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
