"""Build an exact candidate-level completion gap manifest for Pool-A MD."""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

IDENTITY_FIELDS = ("target_key", "run_id", "candidate_id", "sequence", "sequence_sha256")
BOOLEAN_FIELDS = (
    "md_launched",
    "md_complete",
    "interface_complete",
    "mmgbsa_complete",
    "interface_postgresql_ingested",
    "mmgbsa_postgresql_ingested",
    "postgresql_evidence_complete",
    "pool_s_evidence_complete",
)


def boolean(value: str, label: str) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"invalid boolean for {label}: {value!r}")


def classify(row: dict) -> str:
    if row["pool_s_evidence_complete"] and row["postgresql_evidence_complete"]:
        return "complete"
    if not row["md_launched"]:
        return "not_launched"
    if not row["md_complete"]:
        return "md_running_or_checkpoint_pending"
    if not row["interface_complete"] or not row["mmgbsa_complete"]:
        return "post_md_analysis_pending"
    if not row["postgresql_evidence_complete"]:
        return "postgresql_ingest_pending"
    return "inconsistent"


def inconsistencies(row: dict) -> list[str]:
    issues = []
    if row["md_complete"] and not row["md_launched"]:
        issues.append("md_complete_without_launch")
    if (row["interface_complete"] or row["mmgbsa_complete"]) and not row["md_complete"]:
        issues.append("analysis_complete_without_md_complete")
    if row["interface_postgresql_ingested"] and not row["interface_complete"]:
        issues.append("interface_ingested_without_analysis")
    if row["mmgbsa_postgresql_ingested"] and not row["mmgbsa_complete"]:
        issues.append("mmgbsa_ingested_without_analysis")
    return issues


def build(source_rows: list[dict[str, str]]) -> dict:
    candidates = []
    seen = set()
    for source in source_rows:
        identity = (source["run_id"], source["candidate_id"])
        if identity in seen:
            raise ValueError(f"duplicate Pool-A MD identity: {identity}")
        seen.add(identity)
        row = dict(source)
        for field in BOOLEAN_FIELDS:
            row[field] = boolean(source[field], f"{source['candidate_id']}.{field}")
        stage = classify(row)
        issues = inconsistencies(row)
        candidates.append(
            {
                **{field: row[field] for field in IDENTITY_FIELDS},
                "pool_a_rank": int(row["pool_a_rank"]),
                "stage": stage,
                "md_launched": row["md_launched"],
                "md_complete": row["md_complete"],
                "interface_complete": row["interface_complete"],
                "mmgbsa_complete": row["mmgbsa_complete"],
                "interface_postgresql_ingested": row[
                    "interface_postgresql_ingested"
                ],
                "mmgbsa_postgresql_ingested": row["mmgbsa_postgresql_ingested"],
                "issues": issues,
            }
        )
    candidates.sort(
        key=lambda row: (row["target_key"], row["stage"], row["pool_a_rank"])
    )
    by_target = defaultdict(Counter)
    stages = Counter()
    issue_counts = Counter()
    for row in candidates:
        stages[row["stage"]] += 1
        by_target[row["target_key"]][row["stage"]] += 1
        issue_counts.update(row["issues"])
    return {
        "schema_version": "ampgent.pool-a-md-gap-manifest.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "candidate_count": len(candidates),
        "stage_counts": dict(sorted(stages.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "targets": {
            target: {
                "candidate_count": sum(counts.values()),
                "stage_counts": dict(sorted(counts.items())),
            }
            for target, counts in sorted(by_target.items())
        },
        "canonical_completion_definition": (
            "candidate manifest succeeded at 1 ns NPT + 50 ns NVT; canonical interface and "
            "MM/GBSA analyses complete; both exact run/candidate evaluations ingested in PostgreSQL"
        ),
        "noncanonical_smoke_outputs_counted": False,
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.candidates.open(newline="", encoding="utf-8") as stream:
        payload = build(list(csv.DictReader(stream)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
