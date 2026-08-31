from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pepagent.autoresearch_planner import (
    _hydrophobic_fraction,
    _sequence_prescreen,
    _unique_de_novo_sequence,
)
from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text
from pepagent.sequence_family import cluster_sequence_families

BRANCHES = ("acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa")


async def _historical_sequences() -> set[str]:
    async with SessionFactory() as session:
        return set(await session.scalars(select(Candidate.sequence).distinct()))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


async def _run(args: argparse.Namespace) -> None:
    historical = await _historical_sequences()
    additional_references: set[str] = set()
    for path in args.additional_reference_csv:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            additional_references.update(
                str(row["sequence"]).strip().upper() for row in csv.DictReader(stream)
            )
    if any(
        not sequence or set(sequence) - set("ACDEFGHIKLMNPQRSTVWY")
        for sequence in additional_references
    ):
        raise ValueError("additional family reference contains a non-canonical peptide")
    all_references = historical | additional_references
    historical_hashes = {sha256_text(sequence) for sequence in all_references}
    known_sequences = set(additional_references)
    generated_sequences: set[str] = set()
    rows: list[dict[str, Any]] = []
    for branch_index, branch_key in enumerate(BRANCHES):
        for rank in range(args.per_branch):
            seed = args.seed + branch_index * 100_000 + rank
            sequence = _unique_de_novo_sequence(
                branch_key=branch_key,
                seed=seed,
                known_sequences=known_sequences,
                excluded_sequence_sha256s=historical_hashes,
            )
            generated_sequences.add(sequence)
            known_sequences.add(sequence)
            instability, maximum_hydrophobic_run, net_charge = _sequence_prescreen(sequence)
            rows.append(
                {
                    "branch_key": branch_key,
                    "generation": 1,
                    "proposal_rank": rank,
                    "seed": seed,
                    "operator_id": "autoresearch-rule-de-novo-v3",
                    "sequence": sequence,
                    "sequence_sha256": sha256_text(sequence),
                    "guruprasad_instability_index": f"{instability:.6f}",
                    "maximum_hydrophobic_run": maximum_hydrophobic_run,
                    "hydrophobic_fraction": f"{_hydrophobic_fraction(sequence):.6f}",
                    "net_charge_ph7_4": f"{net_charge:.6f}",
                    "historical_exact_replay": "false",
                    "score_all_status": "pending",
                }
            )

    assignments = {
        item.sequence: item
        for item in cluster_sequence_families(all_references | generated_sequences)
    }
    historical_family_keys = {assignments[sequence].family_key for sequence in historical}
    all_reference_family_keys = {
        assignments[sequence].family_key for sequence in all_references
    }
    for row in rows:
        assignment = assignments[str(row["sequence"])]
        row.update(
            {
                "family_key_80_80": assignment.family_key,
                "family_representative_sequence": assignment.representative_sequence,
                "combined_family_size": assignment.family_size,
                "new_family_relative_to_postgresql_history": str(
                    assignment.family_key not in historical_family_keys
                ).lower(),
                "new_family_relative_to_all_references": str(
                    assignment.family_key not in all_reference_family_keys
                ).lower(),
                "diversity_qualified": str(
                    assignment.family_key not in all_reference_family_keys
                ).lower(),
            }
        )
    rows.sort(key=lambda row: (str(row["branch_key"]), int(row["proposal_rank"])))
    _write_csv(args.output_csv, rows)

    summary_rows: list[dict[str, Any]] = []
    for branch_key in BRANCHES:
        branch_rows = [row for row in rows if row["branch_key"] == branch_key]
        summary_rows.append(
            {
                "branch_key": branch_key,
                "proposal_count": len(branch_rows),
                "distinct_family_count": len(
                    {row["family_key_80_80"] for row in branch_rows}
                ),
                "new_family_count_relative_to_postgresql_history": len(
                    {
                        row["family_key_80_80"]
                        for row in branch_rows
                        if row["new_family_relative_to_postgresql_history"] == "true"
                    }
                ),
                "new_family_count_relative_to_all_references": len(
                    {
                        row["family_key_80_80"]
                        for row in branch_rows
                        if row["new_family_relative_to_all_references"] == "true"
                    }
                ),
            }
        )
    _write_csv(args.summary_csv, summary_rows)
    receipt = {
        "schema_version": "ampgent.autoresearch-de-novo-family-probe.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "operator_id": "autoresearch-rule-de-novo-v3",
        "historical_sequence_count": len(historical),
        "additional_reference_sequence_count": len(additional_references),
        "proposal_count": len(rows),
        "distinct_generated_family_count": len(
            {row["family_key_80_80"] for row in rows}
        ),
        "new_family_count_relative_to_postgresql_history": len(
            {
                row["family_key_80_80"]
                for row in rows
                if row["new_family_relative_to_postgresql_history"] == "true"
            }
        ),
        "new_family_count_relative_to_all_references": len(
            {
                row["family_key_80_80"]
                for row in rows
                if row["new_family_relative_to_all_references"] == "true"
            }
        ),
        "additional_reference_csv_sha256s": [
            sha256_file(path) for path in args.additional_reference_csv
        ],
        "branch_summary": summary_rows,
        "output_csv_sha256": sha256_file(args.output_csv),
        "summary_csv_sha256": sha256_file(args.summary_csv),
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output_json.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-branch", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260831)
    parser.add_argument(
        "--additional-reference-csv",
        type=Path,
        action="append",
        default=[],
    )
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--summary-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
