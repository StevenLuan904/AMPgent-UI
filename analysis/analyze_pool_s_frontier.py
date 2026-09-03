"""Build target-local, nonweighted provisional Pool-S MD Pareto fronts."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

OBJECTIVES = {
    "interface_rmsd_mean_nm": "lower",
    "native_contact_fraction_mean": "higher",
    "mmgbsa_mean_kcal_mol": "lower",
}
IDENTITY_FIELDS = ("target_key", "run_id", "candidate_id", "sequence", "sequence_sha256")
SUPPORTING_FIELDS = (
    "pool_a_rank",
    "rosetta_median_dg_reu",
    "interface_rmsd_max_nm",
    "native_contact_fraction_min",
    "key_contact_count",
    "key_contact_occupancy_mean",
    "key_contact_occupancy_max",
    "hydrogen_bond_occupancy",
    "salt_bridge_occupancy",
    "water_bridge_occupancy",
    "peptide_departed",
    "maximum_departure_duration_ps",
    "maximum_peptide_com_shift_nm",
    "mmgbsa_ci95_lower_kcal_mol",
    "mmgbsa_ci95_upper_kcal_mol",
    "mmgbsa_frame_count",
    "decomposition_residue_count",
)
INTEGER_FIELDS = {
    "pool_a_rank",
    "key_contact_count",
    "mmgbsa_frame_count",
    "decomposition_residue_count",
}


def boolean(value: str) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def typed(row: dict[str, str]) -> dict:
    result = dict(row)
    for key in ("pool_s_evidence_complete", "postgresql_evidence_complete"):
        result[key] = boolean(row[key])
    if result["pool_s_evidence_complete"] and result["postgresql_evidence_complete"]:
        for key in OBJECTIVES:
            value = float(row[key])
            if not math.isfinite(value):
                raise ValueError(f"non-finite {key} for {row['candidate_id']}")
            result[key] = value
        for key in SUPPORTING_FIELDS:
            if key == "peptide_departed":
                result[key] = boolean(row[key])
            elif row[key] == "":
                result[key] = None
            elif key in INTEGER_FIELDS:
                result[key] = int(row[key])
            else:
                value = float(row[key])
                if not math.isfinite(value):
                    raise ValueError(f"non-finite {key} for {row['candidate_id']}")
                result[key] = value
    return result


def better(left: float, right: float, direction: str) -> bool:
    if math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-8):
        return False
    return left < right if direction == "lower" else left > right


def dominates(left: dict, right: dict) -> bool:
    comparisons = [
        better(left[key], right[key], direction)
        for key, direction in OBJECTIVES.items()
    ]
    reverse = [
        better(right[key], left[key], direction)
        for key, direction in OBJECTIVES.items()
    ]
    return any(comparisons) and not any(reverse)


def pareto_front(rows: list[dict]) -> list[dict]:
    return [row for row in rows if not any(dominates(other, row) for other in rows)]


def candidate_payload(row: dict) -> dict:
    fields = (*IDENTITY_FIELDS, *OBJECTIVES, *SUPPORTING_FIELDS)
    return {key: row.get(key) for key in fields}


def analyze(rows: list[dict]) -> dict:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["target_key"]].append(row)
    targets = {}
    frontier_rows = []
    for target, target_rows in sorted(grouped.items()):
        complete = [
            row
            for row in target_rows
            if row["pool_s_evidence_complete"]
            and row["postgresql_evidence_complete"]
        ]
        front = sorted(
            pareto_front(complete),
            key=lambda row: (int(row["pool_a_rank"]), row["candidate_id"]),
        )
        frontier_rows.extend(front)
        leaders = {}
        for key, direction in OBJECTIVES.items():
            if not complete:
                leaders[key] = None
                continue
            leader = min(complete, key=lambda row: row[key]) if direction == "lower" else max(
                complete, key=lambda row: row[key]
            )
            leaders[key] = candidate_payload(leader)
        targets[target] = {
            "pool_a_candidate_count": len(target_rows),
            "md_and_postgresql_complete_count": len(complete),
            "provisional_frontier_count": len(front),
            "objective_conflict_retained": len(front) > 1,
            "endpoint_leaders": leaders,
            "provisional_frontier": [candidate_payload(row) for row in front],
        }
    return {
        "schema_version": "ampgent.pool-s-provisional-md-pareto.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "scope": "target-local; no cross-target energy comparison",
        "status": "provisional_until_full_pool_a_md_completion",
        "objectives": OBJECTIVES,
        "weighted_total_used": False,
        "pool_a_candidate_count": len(rows),
        "md_and_postgresql_complete_count": sum(
            row["pool_s_evidence_complete"] and row["postgresql_evidence_complete"]
            for row in rows
        ),
        "provisional_frontier_count": len(frontier_rows),
        "targets": targets,
        "limitations": [
            "computed MD and MM/GBSA evidence; not experimental activity or affinity",
            "MM/GBSA confidence intervals describe within-trajectory block uncertainty",
            "frontier membership may change as pending Pool-A trajectories complete",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frontier-csv", type=Path, required=True)
    args = parser.parse_args()
    with args.candidates.open(newline="", encoding="utf-8") as stream:
        rows = [typed(row) for row in csv.DictReader(stream)]
    payload = analyze(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    frontier = [
        item
        for target in payload["targets"].values()
        for item in target["provisional_frontier"]
    ]
    args.frontier_csv.parent.mkdir(parents=True, exist_ok=True)
    temporary_csv = args.frontier_csv.with_name(
        f".{args.frontier_csv.name}.{os.getpid()}.tmp"
    )
    with temporary_csv.open("w", newline="", encoding="utf-8") as stream:
        fields = list(frontier[0]) if frontier else IDENTITY_FIELDS
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(frontier)
    temporary_csv.replace(args.frontier_csv)


if __name__ == "__main__":
    main()
