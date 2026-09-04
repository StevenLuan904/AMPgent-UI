"""Close ANGPT1 v2b after exact PG materialization and QD readback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def close(report_dir: Path) -> dict[str, Any]:
    old = _json(report_dir / "close_receipt.json")
    history = _json(report_dir / "pg_exact_history_receipt.json")
    resume = report_dir / "resume_v2b"
    materialization = _json(resume / "materialization_receipt.json")
    readback = _json(resume / "pg_materialization_readback.json")
    qd_evidence = _json(resume / "qd_evidence_persistence_receipt.json")
    coarse = _json(resume / "coarse5_prepared" / "coarse5_prepared_receipt.json")
    if (
        history.get("rejected_occurrence_count") != 0
        or history.get("materialization_allowed") is not True
        or len(history.get("pg_new_hashes") or []) != 2
    ):
        raise ValueError("exact PG history is not verified as two PG-new contributors")
    if (
        materialization.get("materialized_or_reused_in_run_count") != 2
        or not readback.get("complete")
        or readback.get("candidate_count") != 2
        or readback.get("evaluation_count") != 34
        or readback.get("evaluation_count_per_candidate") != [17, 17]
        or readback.get("drift") != 0
    ):
        raise ValueError("materialization readback is incomplete or drifted")
    if (
        qd_evidence.get("status") != "readback_verified"
        or qd_evidence.get("authoritative_candidate_count") != 2
        or qd_evidence.get("qd_evaluation_count") != 6
        or qd_evidence.get("new_cell_count") != 2
        or qd_evidence.get("replacement_count") != 0
        or qd_evidence.get("drift") != 0
        or qd_evidence.get("candidate_mutations") != 0
        or qd_evidence.get("historical_runs_modified") is not False
    ):
        raise ValueError("QD evidence is not readback-verified and append-only")
    if (
        coarse.get("candidate_count") != 2
        or coarse.get("nstruct") != 5
        or coarse.get("dispatch_allowed") is not False
        or coarse.get("pool_a_admitted_count") != 0
    ):
        raise ValueError("coarse5 queue is not prepared-only for both candidates")
    source_provisional_qd = {
        "status": "provisional_only",
        "fixed_cell_count": old["archive_baseline"]["covered_cell_count"]
        + old["archive_baseline"]["empty_cell_count"],
        "quality_eligible_count": old["qd_quality_eligible_count"],
        "new_cell_count": old["qd_new_cell_count"],
        "replacement_count": old["qd_replacement_count"],
        "formal_pg_new": False,
        "future_priority_only": True,
    }
    final_qd = {
        "status": "formal_pg_new_materialized",
        "formal_pg_new": True,
        "future_priority_only": False,
        "historical_pg_gate": "verified_exact_scan",
        "fixed_qd_cell_count": source_provisional_qd["fixed_cell_count"],
        "quality_eligible_count": 2,
        "new_cell_count": 2,
        "replacement_count": 0,
        "materialized_candidate_count": 2,
        "materialized_contribution_counts": {"new_cell": 2, "replacement": 0},
        "materialized_qd_evaluation_count": 6,
        "archive_replay_evidence": True,
        "source_provisional_qd_sha256": qd_evidence["source_qd_sha256"],
        "evidence_tool_call_id": qd_evidence["evidence_tool_call_id"],
    }
    return {
        "schema_version": "ampgent.angpt1-pepmlm-qd-neighbor-v2b-pg-close.1",
        "status": "closed_formal_pg_new",
        "target_key": "angpt1",
        "source": old["source"],
        "operator_id": old["operator_id"],
        "proposal_count": old["proposal_count"],
        "formal_12_complete_count": old["formal_12_complete_count"],
        "display_eligible_count": old["display_eligible_count"],
        "calibrated_support_ge_2_count": old["calibrated_support_ge_2_count"],
        "challenger_reviewed_count": old["challenger_reviewed_count"],
        "challenger_no_conflict_count": old["challenger_no_conflict_count"],
        "apex_status": old["apex_status"],
        "peptiverse_status": old["peptiverse_status"],
        "source_provisional_qd": source_provisional_qd,
        "final_qd": final_qd,
        "persistence": {
            "alembic_version": history["alembic_version"],
            "index_name": history["index_name"],
            "index_flags": history["index_flags"],
            "index_limitation": "legacy_invalid_index_exact_scan_fallback",
            "historical_candidate_hits": history["candidate_hit_count"],
            "historical_score_all_hits": history["operational_score_all_hit_count"],
            "pg_new_count": len(history["pg_new_hashes"]),
            "run_id": materialization["operational_run_id"],
            "authoritative_candidate_count": readback["candidate_count"],
            "evaluation_count": readback["evaluation_count"],
            "evaluation_count_per_candidate": readback[
                "evaluation_count_per_candidate"
            ],
            "tool_call_id": materialization["tool_call_id"],
            "qd_evidence_tool_call_id": qd_evidence["evidence_tool_call_id"],
            "qd_evidence_evaluation_count": qd_evidence["qd_evaluation_count"],
            "exact_binding": readback["exact_subject_run_binding"]
            and readback["exact_tool_call_binding"],
            "replay": readback["replay_check"],
            "drift": readback["drift"],
            "historical_runs_modified": False,
            "pool_a_admitted": False,
        },
        "prepared_coarse5": {
            "candidate_count": coarse["candidate_count"],
            "task_key_count": coarse["task_key_count"],
            "nstruct": coarse["nstruct"],
            "dispatch_allowed": coarse["dispatch_allowed"],
            "status": coarse["status"],
            "existing_decoys_total": coarse["existing_decoys_total"],
            "remaining_decoys_total": coarse["remaining_decoys_total"],
        },
        "scientific_increment": "2 ANGPT1 formal PG-new QD new-cell candidates closed",
        "remote_compute_used": False,
        "gpu_rosetta_md_submitted": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    receipt = close(args.report_dir)
    output = args.report_dir / "pg_materialization_close_receipt.json"
    output.write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
