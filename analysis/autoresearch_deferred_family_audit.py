from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.sequence_family import cluster_sequence_families


def _read_rows(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows.extend(csv.DictReader(stream))
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write an empty deferred family audit")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(args: argparse.Namespace) -> None:
    rows = _read_rows(args.input_csv)
    if not rows:
        raise ValueError("deferred family audit input is empty")
    sequences = [row["sequence"].strip().upper() for row in rows]
    if len(set(sequences)) != len(sequences):
        raise ValueError("deferred family audit input contains duplicate sequences")
    references = {
        row["sequence"].strip().upper() for row in _read_rows(args.reference_csv)
    }
    if any(not sequence for sequence in references):
        raise ValueError("deferred family reference contains an empty sequence")
    assignments = {
        item.sequence: item
        for item in cluster_sequence_families(references | set(sequences))
    }
    reference_family_keys = {assignments[sequence].family_key for sequence in references}
    annotated: list[dict[str, Any]] = []
    for row, sequence in zip(rows, sequences, strict=True):
        family = assignments[sequence]
        annotated.append(
            {
                **row,
                "sequence": sequence,
                "family_key_80_80": family.family_key,
                "family_representative_sequence": family.representative_sequence,
                "combined_family_size": family.family_size,
                "family_clustering_scope": (
                    "frozen_csv_references_plus_current_without_postgresql"
                ),
                "family_method": "ungapped_identity_0.8_coverage_0.8_connected_components",
                "new_family_relative_to_all_references": str(
                    family.family_key not in reference_family_keys
                ).lower(),
                "new_family_relative_to_postgresql_history": "unchecked",
                "historical_exact_replay": "unchecked",
            }
        )
    annotated.sort(
        key=lambda row: (
            str(row["branch_key"]),
            str(row["family_key_80_80"]),
            str(row["sequence"]),
        )
    )
    _write_csv(args.output_csv, annotated)
    branch_summary = []
    for branch in sorted({str(row["branch_key"]) for row in annotated}):
        branch_rows = [row for row in annotated if row["branch_key"] == branch]
        branch_summary.append(
            {
                "branch_key": branch,
                "candidate_count": len(branch_rows),
                "distinct_family_count": len(
                    {row["family_key_80_80"] for row in branch_rows}
                ),
                "new_family_count_relative_to_frozen_references": len(
                    {
                        row["family_key_80_80"]
                        for row in branch_rows
                        if row["new_family_relative_to_all_references"] == "true"
                    }
                ),
            }
        )
    receipt = {
        "schema_version": "ampgent.autoresearch-deferred-family-audit.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "input_csv_sha256s": [sha256_file(path) for path in args.input_csv],
        "reference_csv_sha256s": [sha256_file(path) for path in args.reference_csv],
        "candidate_count": len(annotated),
        "reference_sequence_count": len(references),
        "distinct_family_count": len(
            {row["family_key_80_80"] for row in annotated}
        ),
        "branch_summary": branch_summary,
        "postgresql_history_status": "deferred_unavailable",
        "display_or_promotion_allowed": False,
        "output_csv_sha256": sha256_file(args.output_csv),
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output_json.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", type=Path, action="append", required=True)
    parser.add_argument("--reference-csv", type=Path, action="append", default=[])
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
