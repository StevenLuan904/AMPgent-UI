from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.sequence_family import cluster_sequence_families


async def _historical_sequences() -> set[str]:
    async with SessionFactory() as session:
        return set(await session.scalars(select(Candidate.sequence).distinct()))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write empty family audit")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


async def _run(args: argparse.Namespace) -> None:
    with args.input_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = [
            row
            for row in csv.DictReader(stream)
            if row["excellent_sequence_stage_calibrated"].lower() == "true"
        ]
    if not rows:
        raise ValueError("family audit has no calibrated excellent candidates")
    new_sequences = {row["sequence"] for row in rows}
    if len(new_sequences) != len(rows):
        raise ValueError("family audit input contains duplicate excellent sequences")
    historical = await _historical_sequences()
    assignments = {
        item.sequence: item for item in cluster_sequence_families(historical | new_sequences)
    }
    historical_family_keys = {assignments[sequence].family_key for sequence in historical}
    annotated: list[dict[str, Any]] = []
    for row in rows:
        family = assignments[row["sequence"]]
        annotated.append(
            {
                **row,
                "family_key_80_80": family.family_key,
                "family_representative_sequence": family.representative_sequence,
                "combined_family_size": family.family_size,
                "family_clustering_scope": (
                    "all_postgresql_historical_candidates_plus_current_excellent"
                ),
                "family_method": ("ungapped_identity_0.8_coverage_0.8_connected_components"),
                "new_family_relative_to_postgresql_history": str(
                    family.family_key not in historical_family_keys
                ).lower(),
            }
        )
    annotated.sort(
        key=lambda row: (
            row["branch_key"],
            row["family_key_80_80"],
            -int(row["activity_model_support_count_calibrated"]),
            row["sequence"],
        )
    )
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_csv, annotated)
    branch_summary = []
    for branch_key in sorted({row["branch_key"] for row in annotated}):
        branch_rows = [row for row in annotated if row["branch_key"] == branch_key]
        branch_summary.append(
            {
                "branch_key": branch_key,
                "excellent_candidate_count": len(branch_rows),
                "distinct_family_count": len({row["family_key_80_80"] for row in branch_rows}),
                "new_family_count_relative_to_postgresql_history": len(
                    {
                        row["family_key_80_80"]
                        for row in branch_rows
                        if row["new_family_relative_to_postgresql_history"] == "true"
                    }
                ),
            }
        )
    _write_csv(args.output_summary_csv, branch_summary)
    receipt = {
        "schema_version": "ampgent.autoresearch-rescue-family-audit.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source_csv_sha256": sha256_file(args.input_csv),
        "historical_sequence_count": len(historical),
        "excellent_candidate_count": len(annotated),
        "distinct_family_count": len({row["family_key_80_80"] for row in annotated}),
        "new_family_count_relative_to_postgresql_history": len(
            {
                row["family_key_80_80"]
                for row in annotated
                if row["new_family_relative_to_postgresql_history"] == "true"
            }
        ),
        "branch_summary": branch_summary,
        "output_csv_sha256": sha256_file(args.output_csv),
        "output_summary_csv_sha256": sha256_file(args.output_summary_csv),
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output_json.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-summary-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
