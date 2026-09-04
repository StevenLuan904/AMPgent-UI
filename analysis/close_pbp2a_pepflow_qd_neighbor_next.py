"""Close local-only PBP2a PepFlow QD-neighbor evidence without PG writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pepagent.provenance.hashing import sha256_file


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def close(output_dir: Path) -> dict:
    generation = _load(output_dir / "generation_receipt.json")
    score = _load(output_dir / "score_all" / "receipt.json")
    calibration = _load(output_dir / "calibration_receipt.json")
    challenger = _load(output_dir / "challenger" / "receipt.json")
    qd = _load(output_dir / "provisional_qd.json")
    preflight_empty = generation["provisional_empty_cell_count"]
    qd_receipt = {
        "schema_version": "ampgent.pbp2a-pepflow-qd-neighbor-next-qd.1",
        "status": "provisional_only",
        "source": generation["generation_strategy"],
        "candidate_count": score["proposal_count"],
        "formal_12_complete_count": score["formal_12_complete_count"],
        "display_eligible_count": score["display_eligible_count"],
        "calibrated_support_ge_2_count": calibration["support_ge_2_count"],
        "challenger_no_conflict_count": challenger["challenger_no_conflict_count"],
        "fixed_cell_count": 2160,
        "preflight_empty_cell_count": preflight_empty,
        "quality_eligible_count": qd["quality_eligible_count"],
        "provisional_new_cell_count": qd["new_cell_count"],
        "provisional_replacement_count": qd["replacement_count"],
        "quality_gate_failed_count": qd["quality_gate_failed_count"],
        "formal_pg_new": False,
        "historical_pg_gate": "pending",
        "materialization_status": "proposed_not_materialized",
        "postgresql_reads": 0,
        "postgresql_writes": 0,
        "qd_candidates_csv_sha256": qd["qd_candidates_csv_sha256"],
    }
    (output_dir / "provisional_qd_receipt.json").write_text(
        json.dumps(qd_receipt, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    close_receipt = {
        "schema_version": "ampgent.pbp2a-pepflow-qd-neighbor-next-close.1",
        "target_key": "PBP2a",
        "generation": generation["generation"],
        "seed": generation["seed"],
        "proposal_count": generation["proposal_count"],
        "generation_strategy": generation["generation_strategy"],
        "formal12_count": score["formal_12_complete_count"],
        "display_count": score["display_eligible_count"],
        "calibrated_support_ge_2_count": calibration["support_ge_2_count"],
        "challenger_reviewed_count": challenger["reviewed_candidate_count"],
        "challenger_no_conflict_count": challenger["challenger_no_conflict_count"],
        "challenger_conflict_count": challenger["challenger_conflict_count"],
        "apex_status": "runtime_unavailable",
        "peptiverse_status": "runtime_unavailable",
        "provisional_qd": {
            "status": "provisional_only",
            "fixed_cell_count": 2160,
            "preflight_empty_cell_count": preflight_empty,
            "quality_eligible_count": qd["quality_eligible_count"],
            "new_cell_count": qd["new_cell_count"],
            "replacement_count": qd["replacement_count"],
            "formal_pg_new": False,
        },
        "persistence": {
            "historical_pg_gate": "pending",
            "materialization_status": "proposed_not_materialized",
            "candidate_identity_status": "proposal_only",
            "pool_a_admitted": False,
            "postgresql_reads": 0,
            "postgresql_writes": 0,
            "materialization_writes": 0,
            "exact_history_status": "not_run_historical_pg_gate_pending",
        },
        "execution_safety": {
            "structure_status": "not_created",
            "rosetta_submitted": False,
            "md_submitted": False,
            "gpu_task_submitted": False,
            "scientific_increment": "provisional_two_aa_fallback_scored_no_formal_qd_contribution",
        },
        "runtime_limitations": {
            "missing_verified_runtimes": ["apex", "peptiverse"],
            "challenger_is_not_primary_hard_gate": True,
            "hydrophobic_run_or_fraction_hard_gate": False,
        },
        "artifact_sha256": {
            "proposals_csv": generation["proposal_csv_sha256"],
            "score_all_receipt": sha256_file(output_dir / "score_all" / "receipt.json"),
            "calibrated_scores_csv": calibration["output_csv_sha256"],
            "challenger_review_csv": challenger["challenger_review_csv_sha256"],
            "provisional_qd": sha256_file(output_dir / "provisional_qd.json"),
        },
    }
    (output_dir / "close_receipt.json").write_text(
        json.dumps(close_receipt, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return close_receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    close(parser.parse_args().output_dir.resolve())


if __name__ == "__main__":
    main()
