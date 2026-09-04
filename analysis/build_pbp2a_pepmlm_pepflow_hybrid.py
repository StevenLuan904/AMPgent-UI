"""Build a bounded PBP2a PepMLM-ancestry x PepFlow-donor batch.

This operator is deliberately distinct from the completed pure PepFlow
same-domain round.  It uses only the five no-conflict, target-conditioned
PepMLM elites as immediate parents, keeps the PepMLM ancestry explicit, and
uses real PepFlow rows only as donor evidence.  Candidates are selected only
when their formal descriptor vector lands in a currently empty frozen QD cell.
No score, challenger, materialization, or GPU task is performed here.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import asyncpg

from pepagent.autoresearch_quality_diversity import behavior_vector
from pepagent.developability import sequence_developability_metrics
from pepagent.handoff_metrics import physicochemical_descriptors

OPERATOR_ID = "pbp2a-pepmlm-ancestry-pepflow-donor-qd-gap-v1"
SOURCE_ARM = "PepMLM-target-conditioned-ancestry_x_PepFlow-donor"
TARGET_KEY = "PBP2a"
BRANCH_KEY = "pbp2a"
EDIT_POSITIONS_EXCLUDED = frozenset({2, 10, 19})
AA = frozenset("ACDEFGHIKLMNPQRSTVWY")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def phi(sequence: str) -> list[float]:
    sequence_developability_metrics(sequence)
    descriptors = physicochemical_descriptors(sequence, ph=7.4)
    vector = behavior_vector(
        sequence,
        net_charge=descriptors["net_charge_ph7_4"],
        hydrophobicity=descriptors["hydrophobic_ratio"],
        hydrophobic_moment=descriptors["hydrophobic_moment"],
    )
    return [vector.charge_density, vector.hydrophobicity, vector.hydrophobic_moment, vector.length]


def archive_cells(path: Path) -> tuple[set[str], str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    elites = payload["elites"]
    occupied = {str(item["cell_id"]) for item in elites}
    empty = {str(item) for item in payload["empty_cell_ids"]} - occupied
    return empty, sha256_text(path.read_text(encoding="utf-8")), len(elites)


def candidate_cell(sequence: str, archive: Path) -> str:
    payload = json.loads(archive.read_text(encoding="utf-8"))
    policy = payload["policy"]
    behavior = phi(sequence)
    charge, hydrophobicity, moment, length = behavior

    def bucket(value: float, edges: list[float]) -> int:
        for index, edge in enumerate(edges):
            if value < edge:
                return index
        return len(edges)

    return "q{}-h{}-m{}-l{}".format(
        bucket(charge, [float(x) for x in policy["charge_density_edges"]]),
        bucket(hydrophobicity, [float(x) for x in policy["hydrophobicity_edges"]]),
        bucket(moment, [float(x) for x in policy["hydrophobic_moment_edges"]]),
        bucket(length, [float(x) for x in policy["length_edges"]]),
    )


def select_donors(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in sorted(
        rows,
        key=lambda item: (
            (item.get("donor_fragment") or "").upper(),
            item.get("donor_candidate_id", ""),
        ),
    ):
        if "pepflow" not in (row.get("donor_source") or "").lower():
            continue
        fragment = (row.get("donor_fragment") or "").strip().upper()
        residue = next((item for item in fragment if item in AA), "")
        if not residue or residue in seen:
            continue
        selected.append({**row, "donor_fragment": fragment, "donor_residue": residue})
        seen.add(residue)
    if len(selected) < 2:
        raise ValueError("at least two distinct PepFlow donor residues are required")
    return selected


def changed_edit(parent: str, child: str) -> tuple[str, int, str] | None:
    changed = [
        index for index, pair in enumerate(zip(parent, child, strict=True)) if pair[0] != pair[1]
    ]
    if len(changed) != 1:
        return None
    index = changed[0]
    return parent, index, child[index]


def prior_edits(paths: list[Path]) -> set[tuple[str, int, str]]:
    result: set[tuple[str, int, str]] = set()
    for path in paths:
        for row in read_csv(path):
            parent = (row.get("parent_sequence") or "").strip().upper()
            child = (row.get("sequence") or "").strip().upper()
            edit = changed_edit(parent, child) if parent and child else None
            if edit is not None:
                result.add(edit)
    return result


def _proposal(
    parent: dict[str, str],
    donor: dict[str, str],
    position: int,
    archive: Path,
    *,
    generation: int,
    seed: int,
    target_cell: str,
) -> dict[str, Any]:
    sequence = parent["sequence"].strip().upper()
    residue = donor["donor_residue"]
    child = sequence[:position] + residue + sequence[position + 1 :]
    before, after = phi(sequence), phi(child)
    return {
        "branch_key": BRANCH_KEY,
        "target_key": TARGET_KEY,
        "generation": generation,
        "seed": seed,
        "operator_id": OPERATOR_ID,
        "source_arm": SOURCE_ARM,
        "parent_source": "PepMLM-target-conditioned",
        "donor_source": "PepFlow",
        "parent_run_id": parent["run_id"],
        "parent_candidate_id": parent["candidate_id"],
        "parent_sequence": sequence,
        "parent_sequence_sha256": parent["sequence_sha256"],
        "edit_position_zero_based": position,
        "edit_position_1based": position + 1,
        "edit_length": 1,
        "from_residue": sequence[position],
        "to_residue": residue,
        "edit": f"{sequence[position]}{position + 1}{residue}",
        "donor_candidate_id": donor.get("donor_candidate_id", ""),
        "donor_fragment": donor["donor_fragment"],
        "donor_source_proposal_id": donor.get("source_proposal_id", donor.get("proposal_id", "")),
        "sequence": child,
        "sequence_sha256": sha256_text(child),
        "actual_cell_preflight": candidate_cell(child, archive),
        "target_empty_cell_preflight": target_cell,
        "delta_phi": json.dumps(
            {
                "axes": [
                    "net_charge_over_length",
                    "hydrophobic_ratio",
                    "hydrophobic_moment",
                    "length",
                ],
                "parent_to_child": [
                    after_value - before_value
                    for after_value, before_value in zip(after, before, strict=True)
                ],
            },
            sort_keys=True,
        ),
        "historical_sequence_exclusion": "passed_postgresql_exact_history_gate",
        "prior_edit_exclusion": "passed_operator_history_gate",
        "parent_cell_not_new": True,
        "matched_source_arms_excluded": True,
    }


def generate(
    parents: list[dict[str, str]],
    donors: list[dict[str, str]],
    history_hashes: set[str],
    edits: set[tuple[str, int, str]],
    archive: Path,
    *,
    generation: int,
    seed: int,
    limit: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    if not 1 <= limit <= 12:
        raise ValueError("limit must be in [1, 12]")
    empty_cells, _, _ = archive_cells(archive)
    parent_cells = {candidate_cell(row["sequence"].strip().upper(), archive) for row in parents}
    registered_empty = empty_cells - parent_cells
    if registered_empty & parent_cells:
        raise ValueError("registered empty cells overlap immediate parent cells")
    candidates: list[dict[str, Any]] = []
    seen_hashes = set(history_hashes)
    seen_cells: set[str] = set()
    # Round-robin parents, then positions and donor residues.  Peripheral
    # positions exclude the completed same-domain 2/10/19 operator and avoid
    # the first three residues of each parent.
    for round_index in range(max(1, len(parents))):
        for parent_index, parent in enumerate(parents):
            sequence = parent["sequence"].strip().upper()
            positions = [
                position
                for position in range(3, len(sequence))
                if position not in EDIT_POSITIONS_EXCLUDED and sequence[position] in "AGLNSQTV"
            ]
            for slot, position in enumerate(positions):
                donor = donors[(round_index + parent_index + slot) % len(donors)]
                residue = donor["donor_residue"]
                edit = (sequence, position, residue)
                child = sequence[:position] + residue + sequence[position + 1 :]
                digest = sha256_text(child)
                cell = candidate_cell(child, archive)
                if residue == sequence[position] or digest in seen_hashes or edit in edits:
                    continue
                if cell not in registered_empty or cell in seen_cells:
                    continue
                row = _proposal(
                    parent,
                    donor,
                    position,
                    archive,
                    generation=generation,
                    seed=seed,
                    target_cell=cell,
                )
                candidates.append(row)
                seen_hashes.add(digest)
                edits.add(edit)
                seen_cells.add(cell)
                if len(candidates) >= limit:
                    return candidates, registered_empty
    return candidates, registered_empty


async def pg_history(
    pg_url: str, sequence_hashes: set[str], parents: list[dict[str, str]]
) -> tuple[set[str], dict[str, int]]:
    connection = await asyncpg.connect(pg_url, timeout=5, command_timeout=15)
    try:
        candidates = await connection.fetch(
            """
            select sequence_sha256 from candidates
            where sequence_sha256 = any($1::text[])
            """,
            list(sequence_hashes),
        )
        operational = await connection.fetch(
            """
            select distinct candidate ->> 'sequence_sha256' as sequence_sha256
            from lifecycle_events as event
            cross join lateral jsonb_array_elements(
                case when jsonb_typeof(event.payload_json -> 'output' -> 'candidates') = 'array'
                     then event.payload_json -> 'output' -> 'candidates' else '[]'::jsonb end
            ) as candidate
            where event.event_type = 'operational.call.succeeded'
              and event.payload_json ->> 'purpose' = 'score_all'
              and event.payload_json ->> 'status' = 'succeeded'
              and candidate ->> 'sequence_sha256' = any($1::text[])
            """,
            list(sequence_hashes),
        )
        parent_rows = await connection.fetch(
            """
            select id::text as candidate_id, run_id::text as run_id,
                   sequence, sequence_sha256
            from candidates
            where run_id = $1::uuid and id = any($2::uuid[])
            """,
            parents[0]["run_id"],
            [row["candidate_id"] for row in parents],
        )
    finally:
        await connection.close()
    candidate_hashes = {str(row["sequence_sha256"]) for row in candidates}
    operational_hashes = {
        str(row["sequence_sha256"]) for row in operational if row["sequence_sha256"]
    }
    observed_parents = {
        (
            str(row["candidate_id"]),
            str(row["run_id"]),
            str(row["sequence"]),
            str(row["sequence_sha256"]),
        )
        for row in parent_rows
    }
    expected_parents = {
        (row["candidate_id"], row["run_id"], row["sequence"], row["sequence_sha256"])
        for row in parents
    }
    if observed_parents != expected_parents:
        raise ValueError("authoritative parent identity did not round-trip through PostgreSQL")
    history = candidate_hashes | operational_hashes
    if any(len(item) != 64 for item in history):
        raise ValueError("PostgreSQL history contains malformed sequence hash")
    return history, {
        "candidate_hash_count": len(candidate_hashes),
        "operational_hash_count": len(operational_hashes),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write empty proposal CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


async def run(args: argparse.Namespace) -> None:
    parents = read_csv(args.parents_csv)
    if len(parents) != 5 or len({row["run_id"] for row in parents}) != 1:
        raise ValueError("exactly five parents from one authoritative run are required")
    if any(len(row.get("candidate_id", "")) != 36 for row in parents):
        raise ValueError("parent candidate IDs must be authoritative UUID strings")
    donors = select_donors(read_csv(args.pepflow_csv))
    edits = prior_edits(args.history_csv)
    provisional, _ = generate(
        parents,
        donors,
        set(),
        set(edits),
        args.archive,
        generation=args.generation,
        seed=args.seed,
        limit=args.limit,
    )
    history, pg_counts = await pg_history(
        args.pg_url,
        {str(row["sequence_sha256"]) for row in provisional},
        parents,
    )
    proposals, registered_empty = generate(
        parents,
        donors,
        history,
        set(edits),
        args.archive,
        generation=args.generation,
        seed=args.seed,
        limit=args.limit,
    )
    if not proposals:
        raise ValueError(
            "no PG-new proposal remained after exact history, edit, and empty-cell exclusion"
        )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write_csv(args.output_dir / "proposals.csv", proposals)
    receipt = {
        "schema_version": "ampgent.pbp2a-pepmlm-pepflow-hybrid-generation.1",
        "operator_id": OPERATOR_ID,
        "source_arm": SOURCE_ARM,
        "target_key": TARGET_KEY,
        "parent_run_id": parents[0]["run_id"],
        "parent_candidate_ids": [row["candidate_id"] for row in parents],
        "parent_count": len(parents),
        "generation": args.generation,
        "seed": args.seed,
        "edit_budget": "one_residue_equal_length_substitution",
        "excluded_prior_positions_zero_based": sorted(EDIT_POSITIONS_EXCLUDED),
        "donor_residue_count": len(donors),
        "donor_residues": [row["donor_residue"] for row in donors],
        "prior_edit_count": len(edits),
        "postgresql_history_counts": pg_counts,
        "postgresql_history_union_count": len(history),
        "archive_path": str(args.archive),
        "archive_sha256": sha256_text(args.archive.read_text(encoding="utf-8")),
        "parent_occupied_cells": sorted(
            {candidate_cell(row["sequence"].strip().upper(), args.archive) for row in parents}
        ),
        "registered_target_empty_cells": sorted(registered_empty),
        "parent_empty_cell_overlap_count": 0,
        "proposal_count": len(proposals),
        "proposal_cells": sorted({row["actual_cell_preflight"] for row in proposals}),
        "historical_exact_replay_count": 0,
        "formal12_pending": True,
        "materialized_count": 0,
        "gpu_rosetta_md_submitted": False,
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_text(
        json.dumps(receipt, sort_keys=True, ensure_ascii=False)
    )
    (args.output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parents-csv", type=Path, required=True)
    parser.add_argument("--pepflow-csv", type=Path, required=True)
    parser.add_argument("--history-csv", type=Path, action="append", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--pg-url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generation", type=int, default=105)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--limit", type=int, default=12)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
