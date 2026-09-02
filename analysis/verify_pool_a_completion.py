from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analysis.report_pool_a_live import POOL_A_BALANCE_TARGET, TARGETS


def verify(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "ampgent.pool-a-live-postgresql.2":
        errors.append("schema_version")
    admission_policy = payload.get("pool_a_admission_policy") or {}
    if admission_policy.get("capacity_limit", "missing") is not None:
        errors.append("pool_a_capacity_limit")
    if admission_policy.get("admit_all_qualified") is not True:
        errors.append("pool_a_uncapped_admission")
    if int(admission_policy.get("balance_target_per_target") or 0) != POOL_A_BALANCE_TARGET:
        errors.append("pool_a_balance_target")
    rows = list(payload.get("pool_a_all") or [])
    summaries = payload.get("targets") or {}
    candidate_ids: set[str] = set()
    sequence_hashes: set[str] = set()
    target_counts: dict[str, int] = {}

    for target in TARGETS:
        target_rows = [row for row in rows if row.get("target_key") == target]
        target_counts[target] = len(target_rows)
        summary = summaries.get(target) or {}
        families = [str(row.get("family_key") or "") for row in target_rows]
        ranks = [int(row.get("pool_a_rank") or 0) for row in target_rows]
        if len(target_rows) < POOL_A_BALANCE_TARGET:
            errors.append(f"{target}:balance_target")
        if int(summary.get("pool_a_total_family_count", -1)) != len(target_rows):
            errors.append(f"{target}:summary_count")
        if int(summary.get("pool_a_balance_gap_to_50", -1)) != 0:
            errors.append(f"{target}:balance_gap")
        if summary.get("resource_priority_tier") != "uncapped_growth":
            errors.append(f"{target}:resource_priority_tier")
        if len(families) != len(set(families)) or not all(families):
            errors.append(f"{target}:family_unique")
        if ranks != list(range(1, len(target_rows) + 1)):
            errors.append(f"{target}:rank_sequence")

        for row in target_rows:
            candidate_id = str(row.get("candidate_id") or "")
            sequence_hash = str(row.get("sequence_sha256") or "")
            receipt_hash = str(row.get("receipt_sha256") or "")
            instability = row.get("instability")
            primary_dg = row.get("primary_dg")
            checks = {
                "candidate_id": bool(candidate_id),
                "sequence_sha256": len(sequence_hash) == 64,
                "receipt_sha256": len(receipt_hash) == 64,
                "display_eligible": row.get("display_eligible") is True,
                "activity_support": int(row.get("activity_support") or 0) >= 2,
                "formal_12": int(row.get("formal_metric_count") or 0) == 12,
                "instability": instability is not None
                and math.isfinite(float(instability))
                and float(instability) <= 50.0,
                "non_toxin": row.get("non_toxin") is True,
                "macrel_low": row.get("macrel_low") is True,
                "hemopi2": row.get("hemopi2_covered") is True,
                "apex": row.get("apex_covered") is True,
                "peptiverse": row.get("peptiverse_covered") is True,
                "nstruct": int(row.get("nstruct") or 0) in {5, 20, 200},
                "primary_dg": primary_dg is not None
                and math.isfinite(float(primary_dg))
                and float(primary_dg) < -30.0,
                "primary_aggregation": bool(row.get("primary_aggregation")),
            }
            for key, passed in checks.items():
                if not passed:
                    errors.append(f"{target}:{candidate_id}:{key}")
            if candidate_id in candidate_ids:
                errors.append(f"duplicate_candidate:{candidate_id}")
            if sequence_hash in sequence_hashes:
                errors.append(f"duplicate_sequence:{sequence_hash}")
            candidate_ids.add(candidate_id)
            sequence_hashes.add(sequence_hash)

    top50 = list(payload.get("pool_a_top50") or [])
    if len(top50) != len(TARGETS) * POOL_A_BALANCE_TARGET:
        errors.append("balanced_top50_count")
    return {
        "schema_version": "ampgent.pool-a-completion-verification.1",
        "verified_at": datetime.now(UTC).isoformat(),
        "verified": not errors,
        "balance_target_per_target": POOL_A_BALANCE_TARGET,
        "pool_a_family_count": len(rows),
        "target_family_counts": target_counts,
        "global_candidate_unique": len(candidate_ids) == len(rows),
        "global_sequence_unique": len(sequence_hashes) == len(rows),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = verify(payload)
    result["input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not result["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
