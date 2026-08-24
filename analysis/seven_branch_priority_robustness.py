from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

OBJECTIVES = (
    ("amp_read_log10_mic_um", "min"),
    ("llamp_log10_mic_um", "min"),
    ("macrel_amp_probability", "max"),
    ("toxinpred3_hybrid_score", "min"),
    ("macrel_hemolysis_probability", "min"),
    ("hydrophobic_moment_eisenberg", "max"),
    ("maximum_hydrophobic_run", "min"),
)
ACTIVITY_PERCENTILES = tuple(
    f"{metric}__within_branch_benefit_percentile"
    for metric in (
        "amp_read_log10_mic_um",
        "llamp_log10_mic_um",
        "macrel_amp_probability",
    )
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dominates(
    left: dict[str, str], right: dict[str, str], objectives: tuple[tuple[str, str], ...]
) -> bool:
    no_worse = True
    strictly_better = False
    for metric, direction in objectives:
        a = float(left[metric])
        b = float(right[metric])
        if direction == "min":
            no_worse &= a <= b
            strictly_better |= a < b
        else:
            no_worse &= a >= b
            strictly_better |= a > b
    return no_worse and strictly_better


def _pareto_front(rows: list[dict[str, str]], objectives: tuple[tuple[str, str], ...]) -> set[str]:
    return {
        candidate["candidate_id"]
        for candidate in rows
        if not any(
            _dominates(other, candidate, objectives)
            for other in rows
            if other["candidate_id"] != candidate["candidate_id"]
        )
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()

    with args.input_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1900:
        raise ValueError("expected the frozen 1900-candidate delivery")

    by_branch: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_branch[row["branch_key"]].append(row)

    enriched: list[dict[str, str]] = []
    branch_summary: dict[str, object] = {}
    for branch, branch_rows in sorted(by_branch.items()):
        leave_one_fronts = []
        for omitted_metric, _ in OBJECTIVES:
            objectives = tuple(item for item in OBJECTIVES if item[0] != omitted_metric)
            leave_one_fronts.append((omitted_metric, _pareto_front(branch_rows, objectives)))
        counts: Counter[str] = Counter()
        for row in branch_rows:
            retention = sum(row["candidate_id"] in front for _, front in leave_one_fronts)
            activity_threshold_count = sum(
                all(float(row[column]) >= threshold for column in ACTIVITY_PERCENTILES)
                for threshold in (0.70, 0.75, 0.80)
            )
            strict_developability = (
                float(row["maximum_hydrophobic_run"]) <= 6
                and abs(float(row["net_charge_ph7_4"])) <= 7
            )
            instability_evaluable = row["guruprasad_instability_index__ood"].lower() != "true"
            robust_core = (
                row["priority_tier"] == "A_pareto_no_audit_flag"
                and activity_threshold_count == 3
                and retention >= 6
                and strict_developability
            )
            item = dict(row)
            item["pareto_leave_one_retention_count_of_7"] = str(retention)
            item["three_model_consensus_thresholds_passed_of_3"] = str(activity_threshold_count)
            item["strict_sequence_developability_hint"] = str(strict_developability)
            item["instability_evaluable_in_domain"] = str(instability_evaluable)
            item["robust_priority_core"] = str(robust_core)
            item["robustness_semantics"] = (
                "sensitivity_audit_not_experimental_validation_or_new_hard_gate"
            )
            enriched.append(item)
            counts[f"leave_one_retention:{retention}"] += 1
            counts[f"activity_thresholds_passed:{activity_threshold_count}"] += 1
            counts[f"robust_core:{robust_core}"] += 1
        branch_summary[branch] = {
            "n": len(branch_rows),
            "counts": dict(sorted(counts.items())),
            "leave_one_omissions": [metric for metric, _ in leave_one_fronts],
        }

    enriched.sort(
        key=lambda row: (
            row["branch_key"],
            row["robust_priority_core"] != "True",
            -int(row["pareto_leave_one_retention_count_of_7"]),
            -int(row["three_model_consensus_thresholds_passed_of_3"]),
            int(row["delivery_rank"]),
        )
    )
    robust = [row for row in enriched if row["robust_priority_core"] == "True"]
    output_csv = args.output_prefix.with_suffix(".csv")
    robust_csv = args.output_prefix.with_name(args.output_prefix.name + "_core").with_suffix(".csv")
    output_json = args.output_prefix.with_suffix(".json")
    fieldnames = list(enriched[0])
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(enriched)
    with robust_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(robust)
    payload = {
        "schema_version": "ampgent.seven-branch-priority-robustness.1",
        "created_at": datetime.now(UTC).isoformat(),
        "source_csv": str(args.input_csv),
        "source_sha256": _sha256(args.input_csv),
        "candidate_count": len(rows),
        "method": {
            "pareto_sensitivity": "omit_each_of_7_objectives_once",
            "activity_threshold_sensitivity": [0.70, 0.75, 0.80],
            "strict_developability_hint": "maximum_hydrophobic_run<=6_and_abs_charge<=7",
            "instability": "reported_separately_and_never_used_for_robust_core_when_ood",
            "semantics": "audit_only_does_not_change_frozen_delivery",
        },
        "branch_summary": branch_summary,
        "robust_core_count": len(robust),
        "robust_core_by_branch": dict(sorted(Counter(row["branch_key"] for row in robust).items())),
        "output_csv_sha256": _sha256(output_csv),
        "robust_core_csv": str(robust_csv),
        "robust_core_csv_sha256": _sha256(robust_csv),
    }
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
