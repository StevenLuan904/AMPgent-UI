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
    _adaptive_de_novo_alphabet,
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
    quota_by_branch: dict[str, int] = {}
    for item in args.branch_quota:
        branch_key, separator, raw_count = item.partition("=")
        if separator != "=" or branch_key not in BRANCHES:
            raise ValueError(f"invalid branch quota: {item}")
        count = int(raw_count)
        if count < 1:
            raise ValueError("branch quota must be positive")
        if branch_key in quota_by_branch:
            raise ValueError("de-novo branch quota contains duplicates")
        quota_by_branch[branch_key] = count
    selected_branches = tuple(
        quota_by_branch or dict.fromkeys(args.branch or BRANCHES, args.per_branch)
    )
    if len(set(selected_branches)) != len(selected_branches):
        raise ValueError("de-novo branch selection contains duplicates")
    if not quota_by_branch:
        quota_by_branch = {branch: args.per_branch for branch in selected_branches}
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
    profiles: dict[str, list[str]] = {branch: [] for branch in BRANCHES}
    for path in args.profile_csv:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                branch_key = str(row["branch_key"]).lower()
                if branch_key in profiles:
                    profiles[branch_key].append(str(row["sequence"]).strip().upper())
    profile_alphabets = {
        branch: _adaptive_de_novo_alphabet(sequences)
        for branch, sequences in profiles.items()
    }
    historical_hashes = {sha256_text(sequence) for sequence in all_references}
    known_sequences = set(additional_references)
    generated_sequences: set[str] = set()
    rows: list[dict[str, Any]] = []
    for branch_index, branch_key in enumerate(selected_branches):
        for rank in range(quota_by_branch[branch_key]):
            seed = args.seed + branch_index * 100_000 + rank
            sequence = _unique_de_novo_sequence(
                branch_key=branch_key,
                seed=seed,
                known_sequences=known_sequences,
                excluded_sequence_sha256s=historical_hashes,
                residue_alphabet=profile_alphabets[branch_key],
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
                    "operator_id": "autoresearch-rule-de-novo-v4",
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
    for branch_key in selected_branches:
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
        "operator_id": "autoresearch-rule-de-novo-v4",
        "selected_branches": list(selected_branches),
        "branch_quotas": quota_by_branch,
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
        "profile_csv_sha256s": [sha256_file(path) for path in args.profile_csv],
        "profile_sequence_counts": {
            branch: len(sequences) for branch, sequences in profiles.items()
        },
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
    parser.add_argument("--profile-csv", type=Path, action="append", default=[])
    parser.add_argument("--branch", action="append", choices=BRANCHES, default=[])
    parser.add_argument("--branch-quota", action="append", default=[])
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--summary-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
