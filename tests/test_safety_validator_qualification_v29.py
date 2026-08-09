from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "config/benchmarks/amp_safety_validator_qualification_v29.yaml"
REPORT = ROOT / "reports/amp_safety_validator_v29_qualification_20260809.csv"


def test_v29_is_read_only_and_fail_closed() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["execution_status"] == "completed_no_validator_approved_for_execution"
    assert manifest["model_download_or_execution_performed"] is False
    assert manifest["frozen_v25_cohort_read"] is False
    assert manifest["completed_conclusion"]["approved_for_execution_count"] == 0
    assert manifest["completed_conclusion"]["fail_closed"] is True


def test_every_execution_candidate_must_pass_all_hard_gates() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for candidate in manifest["audited_candidates"]:
        assert candidate["decision"] != "approved_for_execution"
        if candidate["decision"] == "future_protocol_candidate_not_approved_for_execution":
            gates = candidate["gate_status"]
            assert gates["explicit_license"] == "pass"
            assert gates["immutable_weight_identity"] == "pass"
            assert gates["traceable_training_data"] == "pass"
            assert gates["safe_end_to_end_load_path"] == "pending"


def test_report_matches_manifest_decisions() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    expected = {item["validator"]: item["decision"] for item in manifest["audited_candidates"]}
    with REPORT.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 5
    assert {row["validator"]: row["decision"] for row in rows} == expected
    assert all(row["decision"] != "approved_for_execution" for row in rows)
    assert hashlib.sha256(REPORT.read_bytes()).hexdigest() == manifest["completed_conclusion"][
        "report"
    ]["sha256"]
