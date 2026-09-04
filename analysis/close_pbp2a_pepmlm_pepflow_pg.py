"""Close the PBP2a hybrid batch after exact PostgreSQL materialization."""

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
    coarse5 = _load(report_dir / "coarse5_prepared" / "coarse5_prepared_receipt.json")
    if history["rejected_occurrence_count"] != 0:
        raise ValueError("history hit cannot be closed as a new candidate")
    if not readback["complete"] or readback["drift"] != 0:
        raise ValueError("materialization readback is incomplete or drifted")
    if coarse5["dispatch_allowed"] or coarse5["pool_a_admitted_count"]:
        raise ValueError("prepared-only coarse5 contract was widened")
    receipt = {
        "schema_version": "ampgent.pbp2a-pepmlm-pepflow-hybrid-pg-close.1",
        "target_key": "PBP2a",
        "generation": local["generation"],
        "proposal_count": local["proposal_count"],
        "formal12_count": local["formal12_count"],
        "display_count": local["display_count"],
        "support_ge_2_count": local["support_ge_2_count"],
        "challenger": local["challenger"],
        "qd": {
            **local["qd"],
            "materialized_new_cell_count": coarse5["candidate_count"],
        },
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
            "tool_call_count": readback["tool_call_count"],
            "tool_call_id": readback["tool_call_id"],
            "exact_binding": readback["exact_subject_run_binding"]
            and readback["exact_tool_call_binding"],
            "replay": readback["replay_check"],
            "drift": readback["drift"],
            "pool_a_admitted": False,
        },
        "prepared_coarse5": {
            "candidate_count": coarse5["candidate_count"],
            "task_key_count": coarse5["task_key_count"],
            "nstruct": coarse5["nstruct"],
            "dispatch_allowed": coarse5["dispatch_allowed"],
            "status": coarse5["status"],
        },
        "scientific_increment": local["scientific_increment"],
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
