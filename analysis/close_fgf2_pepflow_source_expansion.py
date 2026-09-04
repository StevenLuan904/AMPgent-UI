"""Write the compact close receipt for the FGF2 PepFlow expansion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pepagent.provenance.hashing import sha256_file, sha256_json


def close(root: Path, output: Path) -> dict:
    calibration = json.loads(
        (root / "calibration_receipt.json").read_text(encoding="utf-8")
    )
    qd = json.loads(
        (root / "qd_primary_eb85/qd_summary.json").read_text(encoding="utf-8")
    )
    material = json.loads(
        (root / "materialization_receipt.json").read_text(encoding="utf-8")
    )
    queue = json.loads(
        (root / "coarse5_prepared/coarse5_prepared_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    payload = {
        "schema_version": "ampgent.fgf2-pepflow-source-expansion-close.1",
        "target_key": "fgf2",
        "source": "PepFlow",
        "operator_id": "fgf2-pepflow-source-expansion-1aa-v1",
        "generation": 4,
        "primary_calibration_reference_run_id": calibration["parent_run_ids"]["fgf2"],
        "primary_calibration_provenance": (
            "frozen FGF2 branch reference used by the prior FGF2 PepFlow cohort"
        ),
        "sensitivity_calibrations": {
            "2b8f5096-5e59-5e27-a53d-692adadebd82": {
                "support_ge_2": 2,
                "role": "later PepGLAD materialized small-sample sensitivity",
            },
            "1ff62322-46ce-5422-8e40-a328b729b984": {
                "support_ge_2": 4,
                "role": "single PepMLM increment sensitivity",
            },
        },
        "stage_counts": {
            "proposal": 12,
            "formal12": 12,
            "display": 12,
            "primary_support_ge_2": calibration["support_ge_2_count"],
            "primary_excellent": calibration["excellent_sequence_stage_count"],
            "challenger_reviewed": 12,
            "challenger_no_conflict": 11,
            "challenger_conflict": 1,
            "qd_quality_eligible": qd["eligible_batch_candidate_count"],
            "qd_new_cell": qd["diversity_gain"],
            "qd_replacement": qd["incumbent_replacement_count"],
            "materialized_candidate": material["materialized_or_reused_in_run_count"],
            "inserted_evaluation": material["inserted_evaluation_count"],
            "prepared_coarse5": queue["candidate_count"],
            "pool_a_admitted": 0,
        },
        "primary_calibration_result": "admission_domain",
        "challenger_shadow": {
            "hemopi2": "reviewed",
            "apex": "runtime_unavailable",
            "peptiverse": "runtime_unavailable",
        },
        "materialization": {
            "run_id": material["operational_run_id"],
            "tool_call_id": material["tool_call_id"],
            "replay_count": material["global_exact_replay_skip_count"],
            "historical_runs_modified": material["historical_runs_modified"],
        },
        "coarse5": {
            "status": "prepared_not_dispatched",
            "dispatch_allowed": False,
            "median_dg_gate": -30,
            "queue_sha256": queue["queue_sha256"],
        },
        "preserved_runtime_diagnostic": {
            "status": "wrong_registry_runtime_boundary",
            "path": "score_all/receipt.json",
            "formal12": 0,
            "display": 0,
            "overwritten": False,
        },
        "artifact_sha256s": {
            "generation_receipt": sha256_file(root / "generation_receipt.json"),
            "primary_calibration": sha256_file(root / "calibration_receipt.json"),
            "primary_challenger": sha256_file(
                root / "challenger_primary_eb85/receipt.json"
            ),
            "primary_qd": sha256_file(root / "qd_primary_eb85/qd_summary.json"),
            "materialization": sha256_file(root / "materialization_receipt.json"),
        },
    }
    payload["receipt_payload_sha256"] = sha256_json(payload)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    close(args.root, args.output)


if __name__ == "__main__":
    main()
