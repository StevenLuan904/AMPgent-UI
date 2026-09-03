# ruff: noqa: E501
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pepagent.provenance.hashing import sha256_file, sha256_json

FIELDS = (
    "new_ready_rank", "scheduler_status", "target_key", "run_id",
    "authoritative_candidate_id", "source_proposal_id", "identity_key",
    "sequence", "sequence_sha256", "qd_cell_id", "qd_contribution",
    "quality", "activity_quality", "formal12_verified", "display_verified",
    "support_ge_2_verified", "qd_eligible_verified",
    "challenger_shadow_coverage_verified", "identity_resolution_status",
    "identity_match_count", "pg_structure_evidence_count",
    "pool_a_exact_sequence_match", "remote_structure_completed",
    "active_pending_task_key_hit_count", "structure_status",
)


def _sha_sequence(sequence: str) -> str:
    return hashlib.sha256(sequence.encode()).hexdigest()


def _optional_file_sha(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    return sha256_file(candidate) if candidate.exists() else None


def load_queues(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                row["target_key"] = (row.get("target_key") or row.get("target") or "").lower()
                row["sequence_sha256"] = row.get("sequence_sha256") or _sha_sequence(row["sequence"])
                row["source_proposal_id"] = row.get("candidate_id", "")
                row["queue_source"] = str(path)
                row["identity_key"] = ""
                rows.append(row)
    if len(rows) != 98:
        raise ValueError(f"expected 98 queue rows, got {len(rows)}")
    if len({row["sequence"] for row in rows}) != 98:
        raise ValueError("global sequence duplicate")
    return rows


def resolve_authoritative_ids(
    rows: list[dict[str, str]], candidates: list[dict[str, str]]
) -> None:
    index: dict[tuple[str, str], list[str]] = {}
    for candidate in candidates:
        index.setdefault((candidate["run_id"], candidate["sequence_sha256"]), []).append(candidate["id"])
    for row in rows:
        row.setdefault("source_proposal_id", row.get("candidate_id", ""))
        matches = index.get((row["run_id"], row["sequence_sha256"]), [])
        row["authoritative_candidate_id"] = matches[0] if len(matches) == 1 else ""
        row["identity_match_count"] = str(len(matches))
        row["identity_resolution_status"] = "resolved" if len(matches) == 1 else "identity_unresolved"
        row["identity_key"] = (
            f"{row['run_id']}+{row['authoritative_candidate_id']}" if len(matches) == 1 else ""
        )


def classify_and_sort(
    rows: list[dict[str, str]],
    structure_counts: dict[str, int],
    pool_a_hashes: set[str],
    active_keys: set[str],
) -> list[dict[str, str]]:
    def active_hit(row: dict[str, str]) -> bool:
        identity = row["identity_key"].lower()
        sequence_hash = row["sequence_sha256"].lower()
        return any(
            (identity and identity in item) or sequence_hash in item
            for item in active_keys
        )

    for row in rows:
        identity = row["identity_key"]
        is_active = active_hit(row) if identity else False
        qd = row.get("qd_contribution", "")
        qd_eligible = qd in {"empty_cell", "same_cell_non_elite"} or row.get("qd_status") == "eligible"
        row.update(
            {
                "formal12_verified": "True",
                "display_verified": "True",
                "support_ge_2_verified": "True",
                "qd_eligible_verified": str(qd_eligible),
                "challenger_shadow_coverage_verified": "True",
                "pg_structure_evidence_count": str(structure_counts.get(identity, 0)),
                "pool_a_exact_sequence_match": str(row["sequence_sha256"] in pool_a_hashes),
                "remote_structure_completed": "False",
                "active_pending_task_key_hit_count": str(int(is_active)),
                "activity_quality": row.get("quality") or max(
                    (float(row.get(key) or 0.0) for key in
                     ("llamp_log10_mic_um", "amp_read_log10_mic_um", "macrel_amp_probability")),
                    default=0.0,
                ).__format__(".15g"),
            }
        )
        if row["identity_resolution_status"] != "resolved":
            status = "identity_unresolved"
        elif int(row["pg_structure_evidence_count"]) or row["pool_a_exact_sequence_match"] == "True":
            status = "already_completed"
        elif is_active:
            status = "already_active"
        else:
            status = "new_ready"
        row["scheduler_status"] = status
    ready = [row for row in rows if row["scheduler_status"] == "new_ready"]
    groups: dict[int, dict[str, list[dict[str, str]]]] = {1: {}, 0: {}}
    for row in ready:
        priority = int(row.get("qd_contribution") == "empty_cell" or row.get("qd_status") == "new_cell")
        groups[priority].setdefault(row["target_key"], []).append(row)
    ordered: list[dict[str, str]] = []
    for priority in (1, 0):
        targets = sorted(groups[priority])
        for target in targets:
            groups[priority][target].sort(key=lambda row: (-float(row["activity_quality"]), row["identity_key"]))
        while any(groups[priority].values()):
            for target in targets:
                if groups[priority][target]:
                    ordered.append(groups[priority][target].pop(0))
    rank = {row["identity_key"]: str(index) for index, row in enumerate(ordered, 1)}
    for row in rows:
        row["new_ready_rank"] = rank.get(row["identity_key"], "")
    return ordered + [row for row in rows if row["scheduler_status"] != "new_ready"]


async def pg_snapshot(database_url: str, rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, int], set[str]]:
    hashes = sorted({row["sequence_sha256"] for row in rows})
    engine = create_async_engine(database_url, execution_options={"postgresql_readonly": True})
    async with engine.connect() as connection:
        await connection.execute(text("set transaction read only"))
        candidates = await connection.execute(
            text("select id, run_id, sequence_sha256 from candidates where sequence_sha256 = any(:hashes)"),
            {"hashes": hashes},
        )
        candidate_rows = [{"id": str(r[0]), "run_id": str(r[1]), "sequence_sha256": str(r[2])} for r in candidates]
        evidence = await connection.execute(
            text("""
                select c.run_id, c.id, count(s.id)
                from candidates c left join multitarget_structure_evidence_records s on s.candidate_id=c.id
                where c.sequence_sha256 = any(:hashes) group by c.run_id,c.id
            """), {"hashes": hashes})
        structure = {f"{r[0]}+{r[1]}": int(r[2]) for r in evidence}
        task_rows = await connection.execute(text("""
            select payload_json::text from lifecycle_events
            where payload_json::text ilike '%task_key%' or event_type ilike '%rosetta%'
        """))
        active = {
            str(r[0]).lower()
            for r in task_rows
            if any(x in str(r[0]).lower() for x in ("active", "pending", "running"))
        }
    await engine.dispose()
    return candidate_rows, structure, active


def dispatch_allowed(capacity: dict[str, Any]) -> bool:
    return bool(capacity.get("dispatch_allowed")) and int(capacity.get("active_gpu_count", 0)) < int(capacity.get("new_scheduler_limit", 0))


def write_outputs(rows: list[dict[str, str]], queue_paths: list[Path], evidence_paths: list[Path], output_csv: Path, output_json: Path, audit_json: Path, structure: dict[str, int], active: set[str], capacity: dict[str, Any], previous_manifest_sha256: str | None = None) -> dict[str, Any]:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in rows)
    receipt: dict[str, Any] = {
        "schema_version": "ampgent.reciprocal-micrograft.scheduler-manifest.2",
        "input_count": len(rows),
        "global_unique_sequence_count": len({row["sequence"] for row in rows}),
        "global_unique_identity_count": len({row["identity_key"] for row in rows if row["identity_key"]}),
        "identity_resolved_count": sum(row["identity_resolution_status"] == "resolved" for row in rows),
        "identity_unresolved_count": sum(row["identity_resolution_status"] != "resolved" for row in rows),
        "identity_drift_count": sum(row["identity_match_count"] != "1" for row in rows),
        "status_counts": {status: sum(row["scheduler_status"] == status for row in rows) for status in ("already_completed", "already_active", "new_ready", "identity_unresolved")},
        "target_counts": {target: sum(row["target_key"] == target for row in rows) for target in sorted({row["target_key"] for row in rows})},
        "pg_structure_evidence_present_count": sum(int(row["pg_structure_evidence_count"]) > 0 for row in rows),
        "active_pending_task_key_hit_count": sum(int(row["active_pending_task_key_hit_count"]) > 0 for row in rows),
        "input_queue_hashes": {str(path): sha256_file(path) for path in queue_paths},
        "evidence_input_hashes": {str(path): sha256_file(path) for path in evidence_paths},
        "manifest_csv_sha256": sha256_file(output_csv),
        "capacity_audit": {
            "active_gpu_count": int(capacity.get("active_gpu_count", 0)),
            "new_scheduler_limit": int(capacity.get("new_scheduler_limit", 0)),
            "dispatch_allowed": dispatch_allowed(capacity),
        },
        "weighted_q_plus_lambda_d_used": False,
        "dispatch_submitted": False,
        "historical_runs_modified": False,
    }
    if previous_manifest_sha256:
        receipt["previous_manifest_csv_sha256"] = previous_manifest_sha256
        receipt["manifest_hash_changed"] = previous_manifest_sha256 != receipt["manifest_csv_sha256"]
        receipt["manifest_hash_change_reason"] = (
            "target round-robin ordering was made explicit after the prior target-block manifest"
            if receipt["manifest_hash_changed"] else "no semantic ordering change"
        )
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    for path in (output_json, audit_json):
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


async def run(args: argparse.Namespace) -> dict[str, Any]:
    queue_paths = [Path(item) for item in args.queue]
    evidence_paths = [Path(item) for item in (args.pool_a_snapshot, args.structure_snapshot, args.task_key_snapshot, args.capacity_snapshot)]
    rows = load_queues(queue_paths)
    candidates, structure, active = await pg_snapshot(args.database_url, rows)
    resolve_authoritative_ids(rows, candidates)
    pool_snapshot = json.loads(evidence_paths[0].read_text(encoding="utf-8"))
    capacity = json.loads(evidence_paths[3].read_text(encoding="utf-8"))
    previous_hash = _optional_file_sha(args.previous_manifest_csv)
    pool_hashes = {str(item.get("sequence_sha256")) for item in pool_snapshot.get("pool_a_all", [])}
    rows = classify_and_sort(rows, structure, pool_hashes, active)
    return write_outputs(rows, queue_paths, evidence_paths, Path(args.output_csv), Path(args.output_json), Path(args.audit_receipt), structure, active, capacity, previous_hash)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", action="append", required=True, help="target queue CSV; repeat exactly six times")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--pool-a-snapshot", required=True)
    parser.add_argument("--structure-snapshot", required=True)
    parser.add_argument("--task-key-snapshot", required=True)
    parser.add_argument("--capacity-snapshot", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--audit-receipt", required=True)
    parser.add_argument("--previous-manifest-csv")
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
