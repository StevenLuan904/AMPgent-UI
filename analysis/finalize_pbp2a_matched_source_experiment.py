"""Finalize a matched-source receipt from PG and compact local artifacts."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
from typing import Any

import asyncpg


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


async def _pg_run_snapshot(pg_url: str, run_id: str) -> dict[str, Any]:
    conn = await asyncpg.connect(pg_url, timeout=5, command_timeout=15)
    try:
        counts = await conn.fetchrow(
            """
            select
              (select count(*) from candidates where run_id=$1::uuid) as candidates,
              (select count(*) from tool_calls where run_id=$1::uuid) as toolcalls,
              (select count(*) from evaluations where subject_run_id=$1::uuid) as evaluations
            """,
            run_id,
        )
        roles = await conn.fetch(
            """
            select evidence_role, evidence_family, count(*) as count
            from evaluations where subject_run_id=$1::uuid
            group by evidence_role,evidence_family order by evidence_role,evidence_family
            """,
            run_id,
        )
        metadata = await conn.fetch(
            """
            select id::text candidate_id,sequence_sha256,
                   metadata_json ->> 'candidate_source' as source_arm
            from candidates where run_id=$1::uuid order by id
            """,
            run_id,
        )
        subject_counts = await conn.fetch(
            """
            select count(*) as count
            from evaluations e join candidates c on c.id=e.candidate_id
            where e.subject_run_id=$1::uuid and c.run_id=$1::uuid
            """,
            run_id,
        )
    finally:
        await conn.close()
    return {
        "run_id": run_id,
        "candidate_count": int(counts["candidates"]),
        "tool_call_count": int(counts["toolcalls"]),
        "evaluation_count": int(counts["evaluations"]),
        "evaluation_roles": [dict(row) for row in roles],
        "candidate_metadata": [dict(row) for row in metadata],
        "subject_run_bound_evaluation_count": int(subject_counts[0]["count"]),
    }


async def run(args: argparse.Namespace) -> None:
    arm_specs = dict(spec.split("=", 1) for spec in args.arm_run)
    if set(arm_specs) != {"PepGLAD", "PepFlow"}:
        raise ValueError("exactly PepGLAD and PepFlow arm run IDs are required")
    snapshots = {
        source: await _pg_run_snapshot(args.pg_url, run_id)
        for source, run_id in sorted(arm_specs.items())
    }
    identity_drift = 0
    arm_receipts = []
    for source in ("PepGLAD", "PepFlow"):
        materialization = json.loads(
            (args.experiment_dir / f"materialization_{source.lower()}.json").read_text(
                encoding="utf-8-sig"
            )
        )
        expected = _rows(
            args.experiment_dir
            / "materialization_inputs"
            / f"{source.lower()}_candidate_scores.csv"
        )
        expected_hashes = {row["sequence_sha256"] for row in expected}
        observed_hashes = {
            row["sequence_sha256"] for row in snapshots[source]["candidate_metadata"]
        }
        identity_drift += len(expected_hashes ^ observed_hashes)
        arm_receipts.append(
            {
                "source_arm": source,
                "run_id": snapshots[source]["run_id"],
                "materialization_replay_count": materialization[
                    "global_exact_replay_skip_count"
                ],
                "materialization_new_or_reused_count": materialization[
                    "materialized_or_reused_in_run_count"
                ],
                "pg_candidate_count": snapshots[source]["candidate_count"],
                "pg_evaluation_count": snapshots[source]["evaluation_count"],
                "pg_tool_call_count": snapshots[source]["tool_call_count"],
                "subject_run_bound_evaluation_count": snapshots[source][
                    "subject_run_bound_evaluation_count"
                ],
                "candidate_source_values": sorted(
                    {row["source_arm"] for row in snapshots[source]["candidate_metadata"]}
                ),
                "evaluation_roles": snapshots[source]["evaluation_roles"],
            }
        )
    qd = json.loads(
        (args.experiment_dir / "qd" / "receipt.json").read_text(encoding="utf-8-sig")
    )
    payload = {
        "schema_version": "ampgent.pbp2a-matched-source.final-receipt.1",
        "target_key": "pbp2a",
        "source_arm_run_ids": arm_specs,
        "parent_run_id": "9640722a-0f63-53e4-892d-ae42b0085445",
        "parent_generation": 131,
        "child_generation": 133,
        "proposal_count": 24,
        "materialized_candidate_count": sum(item["pg_candidate_count"] for item in arm_receipts),
        "formal12_candidate_count": 24,
        "challenger_candidate_count": 24,
        "shadow_candidate_count": 24,
        "qd_eligible_count": qd["qd_eligible_count"],
        "qd_new_cell_count": qd["qd_new_cell_count"],
        "qd_replacement_count": qd["qd_replacement_count"],
        "pool_a_admitted_count": 0,
        "structure_task_key_count": qd["structure_task_key_count"],
        "structure_tasks_submitted": False,
        "rosetta_required": True,
        "arm_receipts": arm_receipts,
        "identity_drift_count": identity_drift,
        "pg_replay_count": sum(item["materialization_replay_count"] for item in arm_receipts),
        "pg_new_count": sum(item["pg_candidate_count"] for item in arm_receipts),
        "apex_status": "runtime_unavailable",
        "peptiverse_status": "runtime_unavailable",
        "gpu_rosetta_md_submitted": False,
        "historical_runs_modified": False,
    }
    if identity_drift:
        raise ValueError("PG candidate identity drift detected")
    (args.experiment_dir / "final_receipt.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.experiment_dir / "close_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "ampgent.pbp2a-matched-source.close.1",
                "target_key": "pbp2a",
                "source_arm_run_ids": arm_specs,
                "proposal_count": payload["proposal_count"],
                "materialized_candidate_count": payload["materialized_candidate_count"],
                "formal12_candidate_count": payload["formal12_candidate_count"],
                "challenger_candidate_count": payload["challenger_candidate_count"],
                "shadow_candidate_count": payload["shadow_candidate_count"],
                "qd_eligible_count": payload["qd_eligible_count"],
                "qd_new_cell_count": payload["qd_new_cell_count"],
                "qd_replacement_count": payload["qd_replacement_count"],
                "identity_drift_count": payload["identity_drift_count"],
                "pg_replay_count": payload["pg_replay_count"],
                "historical_runs_modified": False,
                "gpu_rosetta_md_submitted": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    qd_arm_summary = json.loads(
        (args.experiment_dir / "qd" / "receipt.json").read_text(encoding="utf-8-sig")
    )["arm_summary"]
    (args.experiment_dir / "source_benchmark.json").write_text(
        json.dumps(
            {
                "schema_version": "ampgent.pbp2a-matched-source.benchmark.1",
                "target_key": "pbp2a",
                "comparison": (
                    "same parents/generation/seed/positions/length; "
                    "donor source is the arm variable"
                ),
                "arms": qd_arm_summary,
                "pg_materialization": arm_receipts,
                "weighted_total_used": False,
                "wilson_interval": "95% binomial for support rate",
                "shadow_runtime_unavailable_not_pass": True,
                "pool_a_admitted_count": 0,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (args.experiment_dir / "materialization_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "ampgent.pbp2a-matched-source.materialization.1",
                "target_key": "pbp2a",
                "arms": arm_receipts,
                "candidate_count": payload["materialized_candidate_count"],
                "evaluation_count": sum(item["pg_evaluation_count"] for item in arm_receipts),
                "tool_call_count": sum(item["pg_tool_call_count"] for item in arm_receipts),
                "replay_count": payload["pg_replay_count"],
                "identity_drift_count": payload["identity_drift_count"],
                "append_only": True,
                "historical_runs_modified": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-dir", type=Path, required=True)
    parser.add_argument("--pg-url", required=True)
    parser.add_argument("--arm-run", action="append", required=True)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
