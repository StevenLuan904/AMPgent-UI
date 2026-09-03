"""Compare candidate-source yields only where denominators are scientifically comparable."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def analyze(payload: dict) -> dict:
    sources = payload["sources"]
    pepglad = sources["pepglad"]
    pepmlm = sources["target_conditioned_pepmlm"]
    pepflow = sources["pepflow"]
    pepmlm_targets = {
        target: {
            **counts,
            "display_yield": ratio(counts["display_eligible"], counts["generated"]),
        }
        for target, counts in sorted(pepmlm["per_target"].items())
    }
    display_yields = {
        target: values["display_yield"] for target, values in pepmlm_targets.items()
    }
    pepflow_bottleneck = (
        "activity_qd_gate"
        if pepflow["display_eligible_count"] > 0
        and pepflow["qd_quality_gate_pass_count"] == 0
        else "not_resolved"
    )
    return {
        "schema_version": "ampgent.candidate-source-yield-analysis.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "display_gate": "ToxinPred3 Non-Toxin; Macrel low; Guruprasad <=50",
        "sources": {
            "pepglad": {
                "strict_unique_candidate_count": pepglad["strict_unique_candidate_count"],
                "activity_support_at_least_two_count": pepglad[
                    "activity_support_at_least_two_count"
                ],
                "activity_support_at_least_two_yield": ratio(
                    pepglad["activity_support_at_least_two_count"],
                    pepglad["strict_unique_candidate_count"],
                ),
                "family_count_80_80": pepglad["family_count_80_80"],
                "target_count": len(pepglad["targets"]),
                "limitation": "strict-library-wide challenger denominator is not asserted",
            },
            "target_conditioned_pepmlm": {
                "generated_unique_candidate_count": pepmlm[
                    "generated_unique_candidate_count"
                ],
                "formal_12_complete_count": pepmlm["formal_12_complete_count"],
                "display_eligible_count": pepmlm["display_eligible_count"],
                "display_yield": ratio(
                    pepmlm["display_eligible_count"],
                    pepmlm["generated_unique_candidate_count"],
                ),
                "per_target": pepmlm_targets,
                "lowest_display_yield_target": min(display_yields, key=display_yields.get),
                "highest_display_yield_target": max(display_yields, key=display_yields.get),
                "limitation": (
                    "challenger review covers a selected cohort, so its rate is not a "
                    "generated-pool yield"
                ),
            },
            "pepflow": {
                "generated_valid_count": pepflow["generated_valid_count"],
                "formal_12_complete_count": pepflow["formal_12_complete_count"],
                "display_eligible_count": pepflow["display_eligible_count"],
                "display_yield": ratio(
                    pepflow["display_eligible_count"], pepflow["generated_valid_count"]
                ),
                "qd_quality_gate_pass_count": pepflow["qd_quality_gate_pass_count"],
                "qd_quality_gate_yield": ratio(
                    pepflow["qd_quality_gate_pass_count"], pepflow["generated_valid_count"]
                ),
                "pool_a_addition_count": pepflow["pool_a_addition_count"],
                "bottleneck": pepflow_bottleneck,
                "limitation": "eight-candidate pilot is too small for source-level ranking",
            },
        },
        "next_experiment": {
            "source": "pepflow",
            "objective": "raise activity-qualified QD-cell yield without relaxing display gates",
            "design": (
                "larger preregistered batch with activity-conditioned or active-family-seeded "
                "sampling; "
                "score all valid unique proposals before QD admission"
            ),
            "primary_outcomes": [
                "display_eligible_count / generated_valid_count",
                "activity_support_at_least_two_count / generated_valid_count",
                "valid_new_qd_cell_count / generated_valid_count",
            ],
            "launch_status": "deferred_while_authorized_gpu_capacity_is_saturated_by_pool_a_md",
        },
        "cross_source_weighted_total_used": False,
        "claim_limit": "in-silico source and model-screening evidence only",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-summary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = analyze(json.loads(args.source_summary.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
