"""Close the GyrA QD-neighbor batch after exact PostgreSQL materialization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def close(report_dir: Path) -> dict[str, Any]:
    local = _load(report_dir / "close_receipt.json")
    history = _load(report_dir / "pg_exact_history_receipt.json")
    materialization = _load(report_dir / "materialization_receipt.json")
    readback = _load(report_dir / "pg_materialization_readback.json")
    qd_evidence = _load(report_dir / "qd_evidence_persistence_receipt.json")
    coarse5 = _load(report_dir / "coarse5_prepared" / "coarse5_prepared_receipt.json")
    if history["rejected_occurrence_count"] != 0:
        raise ValueError("history hit cannot be closed as a new candidate")
    if (
        history.get("exact_history_status") != "verified_exact_scan"
        or history.get("materialization_allowed") is not True
    ):
        raise ValueError("exact PG history is not verified for formal closure")
    if not readback["complete"] or readback["drift"] != 0:
        raise ValueError("materialization readback is incomplete or drifted")
    if not coarse5["dispatch_allowed"] and coarse5["pool_a_admitted_count"] == 0:
        pass
    else:
        raise ValueError("coarse5 queue is not prepared-only")
    if (
        qd_evidence.get("status") != "readback_verified"
        or qd_evidence.get("drift") != 0
        or qd_evidence.get("archive_replay_evidence") is not True
        or qd_evidence.get("candidate_mutations") != 0
        or qd_evidence.get("historical_runs_modified") is not False
    ):
        raise ValueError("QD archive evidence is not append-only and readback-verified")
    source_provisional_qd = local["provisional_qd"]
    materialized_candidate_count = readback["candidate_count"]
    new_cell_count = qd_evidence["new_cell_count"]
    replacement_count = qd_evidence["replacement_count"]
    if (
        materialized_candidate_count != qd_evidence["authoritative_candidate_count"]
        or materialized_candidate_count != history["pg_new_count"]
        or new_cell_count + replacement_count != materialized_candidate_count
        or source_provisional_qd.get("quality_eligible_count")
        != materialized_candidate_count
        or source_provisional_qd.get("new_cell_count") != new_cell_count
        or source_provisional_qd.get("replacement_count") != replacement_count
    ):
        raise ValueError("formal QD counts do not match exact PG materialization")
    final_qd = {
        "status": "formal_pg_new_materialized",
        "formal_pg_new": True,
        "future_priority_only": False,
        "historical_pg_gate": history["exact_history_status"],
        "fixed_qd_cell_count": source_provisional_qd.get(
            "fixed_qd_cell_count", source_provisional_qd["fixed_cell_count"]
        ),
        "quality_eligible_count": qd_evidence["authoritative_candidate_count"],
        "new_cell_count": new_cell_count,
        "replacement_count": replacement_count,
        "materialized_candidate_count": materialized_candidate_count,
        "materialized_contribution_counts": {
            "new_cell": new_cell_count,
            "replacement": replacement_count,
        },
        "materialized_qd_evaluation_count": qd_evidence["qd_evaluation_count"],
        "archive_replay_evidence": True,
        "source_provisional_qd_sha256": qd_evidence["source_qd_sha256"],
        "evidence_tool_call_id": qd_evidence["evidence_tool_call_id"],
    }
    receipt = {
        "schema_version": "ampgent.gyra-pepflow-qd-neighbor-pg-close.1",
        "target_key": "GyrA",
        "generation": local["generation"],
        "proposal_count": local["proposal_count"],
        "strict_intersection_count": history["strict_intersection_count"],
        "formal12_count": local["formal_12_complete_count"],
        "display_count": local["display_eligible_count"],
        "support_ge_2_count": local["calibrated_support_ge_2_count"],
        "challenger": local["challenger"],
        "source_provisional_qd": source_provisional_qd,
        "final_qd": final_qd,
        "persistence": {
            "alembic_version": history["alembic_version"],
            "index_name": "ix_candidate_sequence_sha256",
            "index_flags": history["index_flags"],
            "index_limitation": "legacy_invalid_index_exact_scan_fallback",
            "historical_candidate_hits": history["candidate_hit_count"]
            - history.get("already_materialized_count", 0),
            "already_materialized_count": history.get("already_materialized_count", 0),
            "historical_score_all_hits": history["operational_score_all_hit_count"],
            "pg_new_count": history["pg_new_count"],
            "run_id": materialization["operational_run_id"],
            "authoritative_candidate_count": readback["candidate_count"],
            "evaluation_count": readback["evaluation_count"],
            "evaluation_count_per_candidate": readback[
                "evaluation_count_per_candidate"
            ],
            "tool_call_count": readback["tool_call_count"],
            "tool_call_id": readback["tool_call_id"],
            "qd_evidence_tool_call_id": qd_evidence["evidence_tool_call_id"],
            "qd_evidence_evaluation_count": qd_evidence["qd_evaluation_count"],
            "archive_replay_evidence": qd_evidence["archive_replay_evidence"],
            "exact_binding": readback["exact_subject_run_binding"]
            and readback["exact_tool_call_binding"],
            "replay": readback["replay_check"],
            "drift": readback["drift"],
            "historical_runs_modified": materialization["historical_runs_modified"],
            "pool_a_admitted": False,
        },
        "prepared_coarse5": {
            "candidate_count": coarse5["candidate_count"],
            "task_key_count": coarse5["task_key_count"],
            "nstruct": coarse5["nstruct"],
            "dispatch_allowed": coarse5["dispatch_allowed"],
            "status": "prepared_not_dispatched",
            "existing_decoys_total": 0,
            "remaining_decoys_total": coarse5["candidate_count"] * 5,
        },
        "scientific_increment": (
            "8 formal PG-new QD new-cell candidates closed with exact PG evidence"
        ),
        "remote_compute_used": False,
        "gpu_rosetta_md_submitted": False,
    }
    output = report_dir / "pg_materialization_close_receipt.json"
    output.write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(close(args.report_dir), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
