"""Validate the compact PepGLAD/PepMLM/PepFlow source-evaluation report."""

from __future__ import annotations

import argparse
import json
from itertools import pairwise
from pathlib import Path


def nonnegative(value: object, label: str) -> int:
    result = int(value)
    if result < 0:
        raise ValueError(f"{label} is negative")
    return result


def validate(payload: dict) -> dict:
    if payload.get("schema_version") != "ampgent.candidate-source-evaluation.1":
        raise ValueError("unexpected source-evaluation schema")
    sources = payload["sources"]
    if set(sources) != {"pepglad", "target_conditioned_pepmlm", "pepflow"}:
        raise ValueError("source set drifted")

    pepglad = sources["pepglad"]
    target_counts = [
        nonnegative(value, f"pepglad.targets.{target}")
        for target, value in pepglad["targets"].items()
    ]
    if sum(target_counts) != int(pepglad["strict_unique_candidate_count"]):
        raise ValueError("PepGLAD target counts do not sum to strict unique count")
    if int(pepglad["activity_support_at_least_two_count"]) > sum(target_counts):
        raise ValueError("PepGLAD activity-supported count exceeds strict candidates")
    expected_gate = {
        "toxinpred3": "Non-Toxin",
        "macrel_hemolysis": "low",
        "guruprasad_instability": "<=50",
    }
    if pepglad["strict_display_contract"] != expected_gate:
        raise ValueError("source report display hard gate drifted")

    pepmlm = sources["target_conditioned_pepmlm"]
    per_target = pepmlm["per_target"]
    if len(per_target) != int(pepmlm["target_count"]):
        raise ValueError("PepMLM target count drifted")
    sums = {
        key: sum(
            nonnegative(row[key], f"pepmlm.{target}.{key}")
            for target, row in per_target.items()
        )
        for key in (
            "generated",
            "formal_12",
            "display_eligible",
            "challenger_reviewed",
            "challenger_conflict",
            "challenger_no_conflict",
            "challenger_full_support_no_conflict",
        )
    }
    total_fields = {
        "generated": "generated_unique_candidate_count",
        "formal_12": "formal_12_complete_count",
        "display_eligible": "display_eligible_count",
        "challenger_reviewed": "challenger_reviewed_count",
        "challenger_conflict": "challenger_conflict_count",
        "challenger_no_conflict": "challenger_no_conflict_count",
        "challenger_full_support_no_conflict": "challenger_full_support_no_conflict_count",
    }
    for key, field in total_fields.items():
        if sums[key] != int(pepmlm[field]):
            raise ValueError(f"PepMLM {key} per-target sum drifted")
    if not (
        sums["generated"]
        == sums["formal_12"]
        >= sums["display_eligible"]
        >= sums["challenger_reviewed"]
    ):
        raise ValueError("PepMLM downstream counts are inconsistent")
    if sums["challenger_conflict"] + sums["challenger_no_conflict"] != sums[
        "challenger_reviewed"
    ]:
        raise ValueError("PepMLM challenger outcomes do not partition reviewed candidates")

    pepflow = sources["pepflow"]
    ordered = [
        nonnegative(pepflow[field], f"pepflow.{field}")
        for field in (
            "generated_valid_count",
            "formal_12_complete_count",
            "display_eligible_count",
            "qd_quality_gate_pass_count",
            "pool_a_addition_count",
        )
    ]
    if ordered[0] != ordered[1] or any(left < right for left, right in pairwise(ordered)):
        raise ValueError("PepFlow downstream counts are inconsistent")
    reviewed = nonnegative(pepflow["challenger_reviewed_count"], "pepflow reviewed")
    conflicts = nonnegative(pepflow["challenger_conflict_count"], "pepflow conflict")
    no_conflicts = nonnegative(
        pepflow["challenger_no_conflict_count"], "pepflow no conflict"
    )
    if reviewed != ordered[0] or conflicts + no_conflicts != reviewed:
        raise ValueError("PepFlow challenger outcomes are inconsistent")

    pool = payload["current_pool_a"]
    candidate_count = nonnegative(pool["candidate_count"], "Pool A candidate count")
    database_count = nonnegative(pool["database_candidate_count"], "Pool A DB count")
    missing_count = nonnegative(pool["missing_candidate_count"], "Pool A missing count")
    if database_count + missing_count != candidate_count:
        raise ValueError("Pool A database coverage is inconsistent")
    if sum(int(value) for value in pool["source_counts"].values()) != candidate_count:
        raise ValueError("Pool A source attribution counts do not sum to candidate count")
    return {
        "source_count": len(sources),
        "pepglad_strict_unique_candidate_count": sum(target_counts),
        "pepmlm_formal_12_complete_count": sums["formal_12"],
        "pepflow_formal_12_complete_count": ordered[1],
        "pepflow_pool_a_addition_count": ordered[-1],
        "display_hard_gate": expected_gate,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(json.loads(args.input.read_text(encoding="utf-8"))), indent=2))


if __name__ == "__main__":
    main()
