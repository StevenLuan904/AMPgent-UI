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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dominates(left: dict[str, str], right: dict[str, str]) -> bool:
    no_worse = True
    strictly_better = False
    for metric, direction in OBJECTIVES:
        a = float(left[metric])
        b = float(right[metric])
        if direction == "min":
            no_worse &= a <= b
            strictly_better |= a < b
        else:
            no_worse &= a >= b
            strictly_better |= a > b
    return no_worse and strictly_better


def _pareto_front(rows: list[dict[str, str]]) -> set[str]:
    front: set[str] = set()
    for candidate in rows:
        if not any(
            _dominates(other, candidate)
            for other in rows
            if other["candidate_id"] != candidate["candidate_id"]
        ):
            front.add(candidate["candidate_id"])
    return front


def _audit_flags(row: dict[str, str]) -> list[str]:
    flags: list[str] = []
    if float(row["maximum_hydrophobic_run"]) > 7:
        flags.append("long_hydrophobic_run_gt7")
    if abs(float(row["net_charge_ph7_4"])) > 8:
        flags.append("extreme_abs_charge_gt8")
    if row["guruprasad_instability_index__ood"].lower() == "true":
        flags.append("instability_short_peptide_ood")
    if float(row["guruprasad_instability_index"]) > 40:
        flags.append("instability_index_gt40_non_gating")
    if row.get("conditional_nll__ood", "").lower() == "true":
        flags.append("target_score_ood_ranking_only")
    return flags


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()

    with args.input_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1900 or len({row["candidate_id"] for row in rows}) != 1900:
        raise ValueError("input must contain exactly 1900 globally unique candidates")

    by_branch: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_branch[row["branch_key"]].append(row)

    enriched: list[dict[str, str]] = []
    summary: dict[str, dict[str, int]] = {}
    for branch, branch_rows in sorted(by_branch.items()):
        front = _pareto_front(branch_rows)
        tier_counts: Counter[str] = Counter()
        flag_counts: Counter[str] = Counter()
        for row in branch_rows:
            flags = _audit_flags(row)
            tier_flags = [
                flag
                for flag in flags
                if not flag.endswith("_ood_ranking_only")
                and flag != "instability_short_peptide_ood"
            ]
            on_front = row["candidate_id"] in front
            if on_front and not tier_flags:
                tier = "A_pareto_no_audit_flag"
            elif on_front or not tier_flags:
                tier = "B_pareto_or_no_audit_flag"
            else:
                tier = "C_review_flags"
            item = dict(row)
            item["priority_tier"] = tier
            item["pareto_front_within_branch"] = str(on_front)
            item["audit_flags_non_gating"] = ";".join(flags)
            item["priority_basis"] = "7_objective_unweighted_pareto_plus_non_gating_audit_flags"
            enriched.append(item)
            tier_counts[tier] += 1
            flag_counts.update(flags)
        summary[branch] = {
            "delivered": len(branch_rows),
            "pareto_front": len(front),
            **dict(tier_counts),
            **{f"flag:{key}": value for key, value in sorted(flag_counts.items())},
        }

    tier_order = {
        "A_pareto_no_audit_flag": 0,
        "B_pareto_or_no_audit_flag": 1,
        "C_review_flags": 2,
    }
    enriched.sort(
        key=lambda row: (
            row["branch_key"],
            tier_order[row["priority_tier"]],
            int(row["delivery_rank"]),
        )
    )
    output_csv = args.output_prefix.with_suffix(".csv")
    output_json = args.output_prefix.with_suffix(".json")
    tier_a_csv = args.output_prefix.with_name(args.output_prefix.name + "_tier_a").with_suffix(
        ".csv"
    )
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(enriched[0]))
        writer.writeheader()
        writer.writerows(enriched)
    with tier_a_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(enriched[0]))
        writer.writeheader()
        writer.writerows(
            row for row in enriched if row["priority_tier"] == "A_pareto_no_audit_flag"
        )
    payload = {
        "schema_version": "ampgent.seven-branch-priority-shortlist.1",
        "created_at": datetime.now(UTC).isoformat(),
        "source_csv": str(args.input_csv),
        "source_sha256": _sha256(args.input_csv),
        "candidate_count": len(enriched),
        "method": {
            "pareto_objectives": [
                {"metric": metric, "direction": direction} for metric, direction in OBJECTIVES
            ],
            "instability_semantics": "non_gating_audit_only",
            "target_score_semantics": "within_branch_ranking_only_when_ood",
            "tiers_do_not_change_frozen_delivery": True,
        },
        "branch_summary": summary,
        "csv_sha256": _sha256(output_csv),
        "tier_a_csv": str(tier_a_csv),
        "tier_a_count": sum(row["priority_tier"] == "A_pareto_no_audit_flag" for row in enriched),
        "tier_a_csv_sha256": _sha256(tier_a_csv),
    }
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
