from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pepagent.seven_branch_design import BranchQualityProgress


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def _quality_quota(branch_key: str) -> int:
    return 1000 if branch_key == "target_agnostic_amp" else 150


def build_quality_archive_snapshot(
    delivery_rows: list[dict[str, str]],
    balanced_candidate_ids: set[str],
) -> dict[str, Any]:
    rows_by_branch: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in delivery_rows:
        rows_by_branch[row["branch_key"]].append(row)

    branches: dict[str, dict[str, Any]] = {}
    for branch_key in sorted(rows_by_branch):
        rows = rows_by_branch[branch_key]
        archive_counts = {
            "activity_consensus": sum(
                row["activity_model_concordance"]
                == "three_model_consensus_top_quartile"
                for row in rows
            ),
            "amp_read_endpoint": sum(
                float(
                    row[
                        "amp_read_log10_mic_um__within_branch_benefit_percentile"
                    ]
                )
                >= 0.90
                for row in rows
            ),
            "llamp_endpoint": sum(
                float(
                    row["llamp_log10_mic_um__within_branch_benefit_percentile"]
                )
                >= 0.90
                for row in rows
            ),
            "macrel_endpoint": sum(
                float(
                    row[
                        "macrel_amp_probability__within_branch_benefit_percentile"
                    ]
                )
                >= 0.90
                for row in rows
            ),
            "activity_safety_balance": sum(
                row["candidate_id"] in balanced_candidate_ids for row in rows
            ),
            "stability_degradation": sum(
                not _truthy(row["guruprasad_instability_index__ood"])
                and float(row["guruprasad_instability_index"]) <= 40.0
                for row in rows
            ),
            "novel_family": len({row["family_key_80_80"] for row in rows}),
            "model_disagreement": sum(
                row["activity_model_concordance"]
                == "single_model_optimistic_conflict"
                for row in rows
            ),
        }
        quality_count = sum(
            row["priority_tier"] == "A_pareto_no_audit_flag" for row in rows
        )
        underfilled = tuple(
            key for key, count in archive_counts.items() if count == 0
        )
        quality = BranchQualityProgress(
            branch_key=branch_key,
            quality_quota=_quality_quota(branch_key),
            quality_qualified_count=quality_count,
            archive_counts=archive_counts,
            underfilled_archives=underfilled,
        )
        branches[branch_key] = {
            **quality.model_dump(mode="json"),
            "quality_deficit": quality.quality_quota
            - quality.quality_qualified_count,
            "quality_completion_fraction": (
                quality.quality_qualified_count / quality.quality_quota
            ),
            "snapshot_sha256": quality.sha256(),
        }

    next_branch = min(
        branches,
        key=lambda key: (branches[key]["quality_completion_fraction"], key),
    )
    return {
        "schema_version": "ampgent.seven-branch-quality-archive-snapshot.1",
        "created_at": datetime.now(UTC).isoformat(),
        "semantics": {
            "quality_qualified": "priority_tier=A_pareto_no_audit_flag",
            "archive_membership": "overlapping_non_weighted_fronts",
            "activity_consensus": "all_three_activity_models_at_branch_p75_or_better",
            "single_model_endpoint": "that_model_at_branch_p90_or_better",
            "activity_safety_balance": "frozen_balanced_activity_safety_report_membership",
            "stability_degradation": (
                "Guruprasad_in_domain_and_index_le_40_audit_proxy_not_experimental_half_life"
            ),
            "novel_family": "distinct_seqfam80_family",
            "model_disagreement": "one_model_at_p90_and_other_two_below_branch_median",
            "mutates_formal_delivery": False,
        },
        "branch_count": len(branches),
        "quality_qualified_total": sum(
            item["quality_qualified_count"] for item in branches.values()
        ),
        "quality_quota_total": sum(item["quality_quota"] for item in branches.values()),
        "next_quality_branch": next_branch,
        "branches": branches,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery-csv", type=Path, required=True)
    parser.add_argument("--balanced-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()

    delivery_rows = _read_rows(args.delivery_csv)
    balanced_rows = _read_rows(args.balanced_csv)
    snapshot = build_quality_archive_snapshot(
        delivery_rows,
        {row["candidate_id"] for row in balanced_rows},
    )
    snapshot["sources"] = {
        "delivery_csv": str(args.delivery_csv),
        "delivery_sha256": _sha256(args.delivery_csv),
        "balanced_csv": str(args.balanced_csv),
        "balanced_sha256": _sha256(args.balanced_csv),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "branch_key",
            "quality_quota",
            "quality_qualified_count",
            "quality_deficit",
            "quality_completion_fraction",
            "activity_consensus",
            "amp_read_endpoint",
            "llamp_endpoint",
            "macrel_endpoint",
            "activity_safety_balance",
            "stability_degradation",
            "novel_family",
            "model_disagreement",
            "snapshot_sha256",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for branch_key, branch in snapshot["branches"].items():
            writer.writerow(
                {
                    "branch_key": branch_key,
                    "quality_quota": branch["quality_quota"],
                    "quality_qualified_count": branch["quality_qualified_count"],
                    "quality_deficit": branch["quality_deficit"],
                    "quality_completion_fraction": branch[
                        "quality_completion_fraction"
                    ],
                    **branch["archive_counts"],
                    "snapshot_sha256": branch["snapshot_sha256"],
                }
            )


if __name__ == "__main__":
    main()
