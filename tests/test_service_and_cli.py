"""Tests for the backend integration seam and the command-line interface."""

from __future__ import annotations

import json

from agent.cli import main
from agent.service import health, run_investigation

from conftest import EXAMPLES_DIR, at, tx


class TestService:
    def test_returns_a_plain_json_safe_dict(self, duplicate_case):
        payload = run_investigation(duplicate_case)
        assert isinstance(payload, dict)
        json.dumps(payload)  # must not raise
        assert payload["severity"] == "critical"

    def test_accepts_a_wrapped_body_with_options(self):
        body = {
            "transactions": [
                tx("TXN-1", timestamp=at(0), amount=2500.0),
                tx("TXN-2", timestamp=at(200), amount=2500.0),
            ],
            "options": {"duplicate_window_seconds": 30},
        }
        payload = run_investigation(body)
        assert "near_duplicate_transaction" not in [
            p["pattern_id"] for p in payload["detected_patterns"]
        ]

    def test_unknown_options_are_ignored(self, duplicate_case):
        payload = run_investigation(duplicate_case, options={"nonsense_option": 5})
        assert payload["severity"] == "critical"

    def test_bad_input_returns_a_report_not_an_error(self):
        payload = run_investigation("this is not evidence")
        assert payload["data_quality"]["records_parsed"] == 0
        assert payload["root_cause"]["confidence"] <= 0.1

    def test_health_reports_readiness(self):
        status = health()
        assert status["status"] == "ok"
        assert status["detector_count"] > 10
        assert status["llm_enrichment_enabled"] is False


class TestCli:
    def test_json_output(self, capsys, tmp_path):
        case = tmp_path / "case.json"
        case.write_text(
            json.dumps([tx("TXN-1", timestamp=at(0)), tx("TXN-2", timestamp=at(30))]),
            encoding="utf-8",
        )
        assert main([str(case)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["severity"] in ("low", "medium", "high", "critical")

    def test_summary_output(self, capsys):
        assert main([str(EXAMPLES_DIR / "duplicate_charge.json"), "--summary"]) == 0
        out = capsys.readouterr().out
        assert "SEVERITY : CRITICAL" in out
        assert "ROOT CAUSE" in out
        assert "RECOMMENDED NEXT STEPS" in out

    def test_writes_report_to_file(self, capsys, tmp_path):
        out_path = tmp_path / "report.json"
        assert main(
            [str(EXAMPLES_DIR / "burst_drain.json"), "--compact", "--out", str(out_path)]
        ) == 0
        capsys.readouterr()
        written = json.loads(out_path.read_text(encoding="utf-8"))
        assert written["detected_patterns"]

    def test_every_shipped_example_runs(self, capsys):
        for path in sorted(EXAMPLES_DIR.glob("*.json")):
            assert main([str(path), "--summary"]) == 0
        capsys.readouterr()

    def test_missing_file_is_a_clean_error(self):
        try:
            main(["definitely-not-a-file.json"])
        except SystemExit as exc:
            assert "no such file" in str(exc)
        else:  # pragma: no cover - the CLI must fail here
            raise AssertionError("expected SystemExit")

    def test_invalid_json_is_a_clean_error(self, tmp_path):
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        try:
            main([str(broken)])
        except SystemExit as exc:
            assert "not valid JSON" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected SystemExit")
