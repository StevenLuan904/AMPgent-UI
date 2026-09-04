"""Build a deterministic, read-only targeted Rosetta coarse5 backlog.

The input scope is a run-scoped source benchmark.  Only PostgreSQL-bound
Candidates with a formal/display/challenger/QD gate are considered.  Existing
structure evidence and task-key hits are exclusions, never merged by sequence
alone.  This builder inventories work; it never submits a task.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import asyncpg

TARGETS = ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1")
CONTRIBUTIONS = {"empty_cell", "incumbent_replacement"}
CONFLICT_STATES = {"no_conflict", "cross_model_disagreement_retained"}
TASK_FIELDS = (
    "task_key",
    "rosetta_task_key",
    "candidate_id",
    "sequence_sha256",
    "target_key",
)
CSV_FIELDS = (
    "priority",
    "target_key",
    "target",
    "pool_a_count",
    "balance_gap_to_50",
    "run_id",
    "candidate_id",
    "sequence_sha256",
    "sequence",
    "source",
    "qd_contribution",
    "qd_cell",
    "qd_new_cell",
    "qd_replacement",
    "qd_quality",
    "archive_qd_score",
    "activity_support",
    "formal12",
    "display",
    "challenger_status",
    "quality_eligible",
    "pg_succeeded_evaluations",
    "rosetta_task_key",
    "nstruct",
    "existing_decoy_count",
    "remaining_decoy_count",
    "status",
    "structure_evidence_count",
    "evidence_sources",
)


def truth(value: object) -> bool:
    return str(value).strip().casefold() in {"true", "1", "yes"}


def integer(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def read_json(path: Path) -> dict[str, Any] | list[Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def qd_items(record: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    path = root / record["evidence"]["qd_path"]
    if not path.exists():
        return []
    payload = read_json(path)
    if isinstance(payload, dict) and payload.get("contribution") in CONTRIBUTIONS:
        return [{**payload, "_path": str(path)}]
    if not isinstance(payload, dict):
        return []
    items: list[dict[str, Any]] = []
    for item in payload.get("contributions", []):
        if not isinstance(item, dict) or item.get("contribution") not in CONTRIBUTIONS:
            continue
        if item.get("quality_eligible", payload.get("quality_eligible", True)) is False:
            continue
        items.append(
            {
                **item,
                "quality_eligible": item.get(
                    "quality_eligible", payload.get("quality_eligible", True)
                ),
                "archive_qd_score": payload.get("archive_qd_score"),
                "_path": str(path),
            }
        )
    return items


def score_index(record: dict[str, Any], root: Path) -> dict[str, dict[str, str]]:
    path = root / record["evidence"]["score_path"]
    if not path.exists():
        return {}
    return {
        row.get("sequence_sha256", "").lower(): row
        for row in read_csv(path)
        if row.get("sequence_sha256")
    }


def quality_gate(row: dict[str, str], qd: dict[str, Any]) -> bool:
    return all(
        (
            truth(row.get("formal_12_complete")),
            integer(row.get("formal_metric_count")) == 12,
            truth(row.get("display_eligible")),
            truth(
                row.get("excellent_sequence_stage_calibrated", row.get("excellent_sequence_stage"))
            ),
            integer(
                row.get(
                    "activity_model_support_count_calibrated",
                    row.get("activity_model_support_count"),
                )
            )
            >= 2,
            number(row.get("guruprasad_instability_index"), 1e9) <= 50,
            row.get("toxinpred3_label", "") == "Non-Toxin",
            row.get("macrel_hemolysis_label", "").casefold() == "low",
            truth(qd.get("quality_eligible", True)),
        )
    )


def task_key(target_key: str, run_id: str, candidate_id: str) -> str:
    return f"rosetta5:{target_key}:{run_id}:{candidate_id}"


def normalize_pool_counts(payload: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    targets = payload.get("targets", {})
    for target in TARGETS:
        item = targets.get(target, {})
        counts[target] = integer(
            item.get("pool_a_total_candidate_count", item.get("candidates", 0))
        )
    return counts


def md_snapshot_summary(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict):
        return None
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "observed_at_utc": payload.get("observed_at_utc"),
        "cohort": payload.get("cohort"),
        "current_partition": payload.get("current_partition"),
    }


@lru_cache(maxsize=2)
def task_file_texts(root: Path) -> tuple[tuple[Path, str], ...]:
    allowed = re.compile(r"(structure|rosetta|prepared|coarse5|task|completion|launch)", re.I)
    values: list[tuple[Path, str]] = []
    search_root = root / "reports" if (root / "reports").exists() else root
    listed = subprocess.run(
        ["rg", "--files", str(search_root)],
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    for name in listed.stdout.splitlines():
        path = Path(name)
        if any(
            marker in path.name
            for marker in (
                "targeted_rosetta_coarse5_backlog",
                "remote_structure_inventory_receipt",
            )
        ):
            continue
        if not path.is_file() or not allowed.search(path.name):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            values.append((path, path.read_text(encoding="utf-8", errors="ignore").casefold()))
        except OSError:
            continue
    return tuple(values)


def recursive_text_hits(path: Path, needles: Iterable[str]) -> bool:
    try:
        if path.stat().st_size > 2_000_000:
            return False
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
    except OSError:
        return False
    return any(needle.casefold() in text for needle in needles)


def local_task_hits(root: Path, candidate: dict[str, str]) -> list[dict[str, Any]]:
    needles = (
        candidate["candidate_id"],
        candidate["sequence_sha256"],
        candidate["rosetta_task_key"],
    )
    hits: list[dict[str, Any]] = []
    for path, content in task_file_texts(root):
        if any(needle.casefold() in content for needle in needles):
            label = path.name.casefold()
            status = (
                "prepared"
                if "prepared" in label
                else "active"
                if any(x in label for x in ("launch", "active", "running"))
                else "completed"
                if any(x in label for x in ("completion", "completed"))
                else "evidence"
            )
            hits.append({"path": str(path), "status": status, "existing_decoy_count": 0})
    return hits


def remote_hits(path: Path | None, candidate: dict[str, str]) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    payload = read_json(path)
    values = payload.get("hits", payload) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        return []
    result = []
    needles = {
        candidate[key].casefold() for key in ("candidate_id", "sequence_sha256", "rosetta_task_key")
    }
    for item in values:
        if not isinstance(item, dict):
            continue
        blob = json.dumps(item, sort_keys=True).casefold()
        if any(needle in blob for needle in needles):
            result.append(item)
    return result


def existing_decoys(hits: list[dict[str, Any]]) -> int:
    return min(
        5,
        max(
            (
                integer(item.get(key))
                for item in hits
                for key in ("existing_decoy_count", "decoy_count", "valid_decoy_count", "nstruct")
            ),
            default=0,
        ),
    )


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Balance gap is the primary key.  No q+lambdaD or other weighted total.
    return sorted(
        rows,
        key=lambda row: (
            -row["balance_gap_to_50"],
            -row["qd_new_cell"],
            -row["qd_replacement"],
            -row["qd_quality"],
            -row["activity_support"],
            row["target_key"],
            row["run_id"],
            row["candidate_id"],
        ),
    )


async def pg_snapshot(
    pg_url: str, identities: list[tuple[str, str]]
) -> dict[tuple[str, str], dict[str, Any]]:
    if not identities:
        return {}
    connection = await asyncpg.connect(pg_url, timeout=5, command_timeout=20)
    try:
        rows = await connection.fetch(
            """
            SELECT c.id AS candidate_id, c.run_id, c.sequence, c.sequence_sha256,
                   c.status AS candidate_status, t.accession AS target,
                   c.metadata_json,
                   COUNT(DISTINCT e.id) FILTER (
                       WHERE e.status='succeeded'
                   ) AS succeeded_evaluations,
                   COUNT(DISTINCT s.id) AS structure_evidence_count
            FROM candidates c
            JOIN experiment_runs r ON r.id=c.run_id
            JOIN targets t ON t.id=r.target_id
            LEFT JOIN evaluations e ON e.candidate_id=c.id
            LEFT JOIN multitarget_structure_evidence_records s ON s.candidate_id=c.id
            WHERE (c.run_id, c.sequence_sha256) IN (
                SELECT x.run_id, x.sequence_sha256
                FROM unnest($1::uuid[], $2::text[]) AS x(run_id, sequence_sha256)
            )
            GROUP BY c.id, c.run_id, c.sequence, c.sequence_sha256, c.status,
                     t.accession, c.metadata_json
            """,
            [item[0] for item in identities],
            [item[1] for item in identities],
        )
    finally:
        await connection.close()
    return {(str(row["run_id"]), row["sequence_sha256"].lower()): dict(row) for row in rows}


def build_local(
    benchmark: dict[str, Any],
    root: Path,
    pg_rows: dict[tuple[str, str], dict[str, Any]],
    pool_counts: dict[str, int],
    remote_inventory: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    exclusions: Counter[str] = Counter()
    for record in benchmark.get("records", []):
        target_key = str(record.get("target_key", "")).casefold()
        run_id = str(record.get("run_id", ""))
        if target_key not in TARGETS or record.get("source_scope") != "run":
            exclusions["out_of_scope"] += 1
            continue
        scores = score_index(record, root)
        for qd in qd_items(record, root):
            sequence_sha = str(qd.get("sequence_sha256", qd.get("candidate_id", ""))).casefold()
            identity = (run_id, sequence_sha)
            pg = pg_rows.get(identity)
            if pg is None:
                exclusions["pg_identity_missing"] += 1
                continue
            metadata = pg.get("metadata_json") or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            score = scores.get(sequence_sha, {})
            if not quality_gate(score, qd):
                exclusions["quality_gate"] += 1
                continue
            challenge = str(
                metadata.get(
                    "challenger_conflict_status", score.get("challenger_conflict_status", "")
                )
            )
            if challenge not in CONFLICT_STATES:
                exclusions["challenger"] += 1
                continue
            if integer(pg.get("succeeded_evaluations")) < 15:
                exclusions["pg_evidence_incomplete"] += 1
                continue
            candidate_id = str(pg["candidate_id"])
            key = (run_id, candidate_id, target_key)
            if key in seen:
                exclusions["duplicate_identity"] += 1
                continue
            seen.add(key)
            item = {
                "target_key": target_key,
                "target": str(pg["target"]),
                "pool_a_count": pool_counts.get(target_key, 0),
                "balance_gap_to_50": max(0, 50 - pool_counts.get(target_key, 0)),
                "run_id": run_id,
                "candidate_id": candidate_id,
                "sequence_sha256": sequence_sha,
                "sequence": str(pg["sequence"]),
                "source": str(record.get("source", "")),
                "qd_contribution": str(qd.get("contribution", "")),
                "qd_cell": str(qd.get("cell_id", qd.get("actual_cell_id", ""))),
                "qd_new_cell": int(qd.get("new_cell", qd.get("contribution") == "empty_cell")),
                "qd_replacement": int(
                    qd.get("replacement", qd.get("contribution") == "incumbent_replacement")
                ),
                "qd_quality": number(qd.get("quality", 0)),
                "archive_qd_score": number(qd.get("archive_qd_score", 0)),
                "activity_support": integer(
                    score.get(
                        "activity_model_support_count_calibrated",
                        score.get("activity_model_support_count"),
                    )
                ),
                "formal12": 1,
                "display": 1,
                "challenger_status": challenge,
                "quality_eligible": 1,
                "pg_succeeded_evaluations": integer(pg.get("succeeded_evaluations")),
                "rosetta_task_key": task_key(target_key, run_id, candidate_id),
                "nstruct": 5,
            }
            local = local_task_hits(root, item)
            remote = remote_hits(remote_inventory, item)
            structure_count = integer(pg.get("structure_evidence_count"))
            hits = local + remote
            decoys = existing_decoys(hits)
            if structure_count or hits:
                exclusions["existing_structure_or_task"] += 1
                continue
            item.update(
                {
                    "existing_decoy_count": decoys,
                    "remaining_decoy_count": max(0, 5 - decoys),
                    "status": "backlog_ready",
                    "structure_evidence_count": structure_count,
                    "evidence_sources": "none",
                }
            )
            rows.append(item)
    ordered = sort_rows(rows)
    for index, row in enumerate(ordered, 1):
        row["priority"] = index
    summary = {
        "candidate_count": len(ordered),
        "excluded_counts": dict(sorted(exclusions.items())),
        "target_counts": {
            target: sum(row["target_key"] == target for row in ordered) for target in TARGETS
        },
        "pool_a_counts": pool_counts,
        "remote_inventory_used": str(remote_inventory) if remote_inventory else None,
        "remote_exact_hits": 0,
        "task_submitted": False,
    }
    return ordered, summary


def write_outputs(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    csv_path: Path,
    json_path: Path,
    inputs: dict[str, Any],
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "schema_version": "ampgent.targeted-rosetta-coarse5-backlog.1",
        "observed_at": datetime.now(UTC).isoformat(),
        "identity": "run_id+authoritative_candidate_id+target+sequence_sha256+task_key",
        "scope": "six target-specific branches; target-agnostic excluded",
        "selection": "formal12+display+challenger+QD contribution; no weighted q+lambdaD",
        "nstruct": 5,
        "dispatch_allowed": False,
        "md_snapshot": inputs.get("md_snapshot"),
        "historical_runs_modified": False,
        "inputs": inputs,
        "summary": summary,
        "rows": rows,
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


async def main_async(args: argparse.Namespace) -> None:
    root = args.repo_root.resolve()
    benchmark_path = args.benchmark_json.resolve()
    benchmark = read_json(benchmark_path)
    if not isinstance(benchmark, dict):
        raise ValueError("benchmark JSON must be an object")
    pool_payload = read_json(args.pool_counts_json.resolve())
    if not isinstance(pool_payload, dict):
        raise ValueError("pool counts JSON must be an object")
    pool_counts = normalize_pool_counts(pool_payload)
    identities: list[tuple[str, str]] = []
    for record in benchmark.get("records", []):
        if str(record.get("target_key", "")).casefold() not in TARGETS:
            continue
        for qd in qd_items(record, root):
            identities.append(
                (
                    str(record["run_id"]),
                    str(qd.get("sequence_sha256", qd.get("candidate_id", ""))).casefold(),
                )
            )
    pg_rows = await pg_snapshot(args.pg_url, sorted(set(identities)))
    rows, summary = build_local(benchmark, root, pg_rows, pool_counts, args.remote_inventory)
    summary["pg_identity_rows"] = len(pg_rows)
    inputs = {
        "benchmark_json": str(benchmark_path),
        "benchmark_sha256": hashlib.sha256(benchmark_path.read_bytes()).hexdigest(),
        "pool_counts_json": str(args.pool_counts_json.resolve()),
        "pool_counts_sha256": hashlib.sha256(
            args.pool_counts_json.resolve().read_bytes()
        ).hexdigest(),
        "pg_url_identity": "local authenticated PostgreSQL read-only query",
        "md_snapshot": md_snapshot_summary(args.md_snapshot),
    }
    write_outputs(rows, summary, args.output_csv.resolve(), args.output_json.resolve(), inputs)
    print(
        json.dumps(
            {
                "candidate_count": len(rows),
                "csv": str(args.output_csv.resolve()),
                "json": str(args.output_json.resolve()),
            },
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--benchmark-json", type=Path, required=True)
    parser.add_argument("--pool-counts-json", type=Path, required=True)
    parser.add_argument("--pg-url", default=os.environ.get("PEPAGENT_DATABASE_URL_PLAIN"))
    parser.add_argument("--remote-inventory", type=Path)
    parser.add_argument("--md-snapshot", type=Path)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    if not args.pg_url:
        parser.error("--pg-url or PEPAGENT_DATABASE_URL_PLAIN is required")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
