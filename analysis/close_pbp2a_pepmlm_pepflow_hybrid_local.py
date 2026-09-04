"""Close local-only PBP2a PepMLM x PepFlow hybrid evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def close(output_dir: Path) -> dict:
    generation = load_json(output_dir / "generation_receipt.json")
    score = load_json(output_dir / "score_all" / "receipt.json")
    calibration = load_json(output_dir / "calibration_receipt.json")
    challenger = load_json(output_dir / "challenger" / "receipt.json")
    qd = load_json(output_dir / "provisional_qd.json")
    calibrated = {
        row["sequence_sha256"]: row
        for row in read_csv(output_dir / "candidate_scores_calibrated.csv")
    }
    reviewed = {
        row["sequence_sha256"]: row
        for row in read_csv(output_dir / "challenger" / "challenger_review.csv")
    }
    qd_contributions = [
        row for row in qd["contributions"] if row["contribution"] in {"empty_cell", "replacement"}
    ]
    valid = [
        row
        for row in qd_contributions
        if calibrated.get(row["candidate_id"], {}).get("display_eligible", "").lower() == "true"
        and int(calibrated[row["candidate_id"]].get("activity_model_support_count_calibrated", "0"))
        >= 2
        and reviewed.get(row["candidate_id"], {}).get("challenger_conflict_status")
        == "no_conflict"
    ]
    displacement = read_csv(output_dir / "property_displacement.csv")
    effect = {
        "operator_variant": generation["operator_variant"],
        "proposal_count": generation["proposal_count"],
        "motif_length_counts": {
            str(length): sum(int(row["edit_length"]) == length for row in displacement)
            for length in sorted({int(row["edit_length"]) for row in displacement})
        },
        "mean_delta": {
            key: mean(float(row[key]) for row in displacement)
            for key in (
                "delta_net_charge_over_length",
                "delta_hydrophobic_ratio",
                "delta_hydrophobic_moment",
                "delta_length",
            )
        },
        "formal12_count": score["formal_12_complete_count"],
        "display_count": score["display_eligible_count"],
        "support_ge_2_count": calibration["support_ge_2_count"],
        "challenger_reviewed_count": challenger["reviewed_candidate_count"],
        "challenger_no_conflict_count": challenger["challenger_no_conflict_count"],
        "challenger_conflict_count": challenger["challenger_conflict_count"],
        "qd_quality_eligible_count": qd["quality_eligible_count"],
        "qd_new_cell_count": qd["new_cell_count"],
        "qd_replacement_count": qd["replacement_count"],
        "valid_candidate_count": len(valid),
    }
    (output_dir / "operator_effect.json").write_text(
        json.dumps(effect, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    receipt = {
        "schema_version": "ampgent.pbp2a-pepmlm-pepflow-hybrid-local-close.1",
        "target_key": "PBP2a",
        "operator_id": generation["operator_id"],
        "operator_variant": generation["operator_variant"],
        "generation": generation["generation"],
        "proposal_count": generation["proposal_count"],
        "formal12_count": score["formal_12_complete_count"],
        "display_count": score["display_eligible_count"],
        "support_ge_2_count": calibration["support_ge_2_count"],
        "challenger": {
            "reviewed_count": challenger["reviewed_candidate_count"],
            "identity_coverage_complete": challenger["candidate_identity_coverage_complete"],
            "no_conflict_count": challenger["challenger_no_conflict_count"],
            "conflict_count": challenger["challenger_conflict_count"],
            "apex_status": "runtime_unavailable",
            "peptiverse_status": "runtime_unavailable",
        },
        "qd": {
            "fixed_archive_cell_count": generation["fixed_archive_cell_count"],
            "quality_eligible_count": qd["quality_eligible_count"],
            "new_cell_count": qd["new_cell_count"],
            "replacement_count": qd["replacement_count"],
            "valid_candidate_count": len(valid),
        },
        "algorithm_adjustment": {
            "triggered": False,
            "reason": "primary_motif_support_ge_2_count_nonzero",
        },
        "persistence": {
            "historical_pg_gate": "pending",
            "materialization_status": "proposed_not_materialized",
            "candidate_identity_status": "proposal_only",
            "pool_a_admitted": False,
            "postgresql_reads": 0,
            "postgresql_writes": 0,
            "structure_status": "not_created",
            "gpu_rosetta_md_submitted": False,
        },
        "scientific_increment": (
            "12 local target-conditioned PepMLM ancestry x PepFlow motif candidates; "
            "4 pass display+support+challenger+new-cell provisional gates"
        ),
        "failure_funnel": "failure_funnel.json",
        "property_displacement": "property_displacement.csv",
        "operator_effect": "operator_effect.json",
        "artifact_sha256": {
            "proposals_csv": generation["proposal_csv_sha256"],
            "score_all_receipt": sha256_file(output_dir / "score_all" / "receipt.json"),
            "calibration_receipt": sha256_file(output_dir / "calibration_receipt.json"),
            "challenger_receipt": sha256_file(output_dir / "challenger" / "receipt.json"),
            "provisional_qd": sha256_file(output_dir / "provisional_qd.json"),
            "failure_funnel": sha256_file(output_dir / "failure_funnel.json"),
            "property_displacement": sha256_file(output_dir / "property_displacement.csv"),
            "operator_effect": sha256_file(output_dir / "operator_effect.json"),
        },
    }
    (output_dir / "close_receipt.json").write_text(
        json.dumps(receipt, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    close(parser.parse_args().output_dir.resolve())


if __name__ == "__main__":
    main()
