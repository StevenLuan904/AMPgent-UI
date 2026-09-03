"""Build a bounded, same-domain PBP2a PepFlow conservative-graft batch.

The parent identities are authoritative UUIDs from one frozen parent run.  The
operator changes one residue at fixed positions and uses PepFlow fragments only
as source evidence.  It performs exact sequence-history exclusion and records
prior parent-position-to edits; it does not score or materialize candidates.
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

EDIT_POSITIONS = (2, 10, 19)
AA = set("ACDEFGHIKLMNPQRSTVWY")


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


def changed_edit(parent: str, child: str) -> tuple[str, int, str] | None:
    if not parent or len(parent) != len(child):
        return None
    changed = [i for i, pair in enumerate(zip(parent, child, strict=True)) if pair[0] != pair[1]]
    if len(changed) != 1:
        return None
    index = changed[0]
    return parent, index, child[index]


def prior_edits(paths: list[Path]) -> set[tuple[str, int, str]]:
    result: set[tuple[str, int, str]] = set()
    for path in paths:
        for row in read_csv(path):
            edit = changed_edit(
                (row.get("parent_sequence") or "").strip().upper(),
                (row.get("sequence") or "").strip().upper(),
            )
            if edit is not None:
                result.add(edit)
    return result


async def pg_history(pg_url: str) -> tuple[set[str], dict[str, int]]:
    connection = await asyncpg.connect(pg_url, timeout=5, command_timeout=15)
    try:
        candidates = await connection.fetch(
            "select sequence_sha256 from candidates where sequence_sha256 is not null"
        )
        operational = await connection.fetch(
            """
            select distinct candidate ->> 'sequence_sha256' as sequence_sha256
            from lifecycle_events as event
            cross join lateral jsonb_array_elements(
                case when jsonb_typeof(event.payload_json -> 'output' -> 'candidates') = 'array'
                     then event.payload_json -> 'output' -> 'candidates'
                     else '[]'::jsonb end
            ) as candidate
            where event.event_type = 'operational.call.succeeded'
              and event.payload_json ->> 'purpose' = 'score_all'
              and event.payload_json ->> 'status' = 'succeeded'
            """
        )
    finally:
        await connection.close()
    candidate_hashes = {str(row["sequence_sha256"]) for row in candidates}
    operational_hashes = {
        str(row["sequence_sha256"]) for row in operational if row["sequence_sha256"]
    }
    history = candidate_hashes | operational_hashes
    malformed = {item for item in history if len(item) != 64}
    if malformed:
        raise ValueError("PostgreSQL history contains malformed sequence hash")
    return history, {
        "candidate_hash_count": len(candidate_hashes),
        "operational_hash_count": len(operational_hashes),
    }


def select_pepflow_donors(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
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
        if not fragment or any(residue not in AA for residue in fragment):
            continue
        residue = next((item for item in fragment if item in AA), None)
        if residue is None or residue in seen:
            continue
        result.append({**row, "donor_fragment": fragment, "donor_residue": residue})
        seen.add(residue)
    if not result:
        raise ValueError("no PepFlow donor residue is available")
    return result


def generate(
    parents: list[dict[str, str]],
    donors: list[dict[str, str]],
    *,
    history_hashes: set[str],
    edits: set[tuple[str, int, str]],
    generation: int,
    seed: int,
    limit: int,
) -> list[dict[str, Any]]:
    if not 1 <= limit <= 12:
        raise ValueError("limit must be in [1, 12]")
    result: list[dict[str, Any]] = []
    seen = set(history_hashes)
    for parent_index, parent in enumerate(parents):
        sequence = parent["sequence"].upper()
        for slot, position in enumerate(EDIT_POSITIONS):
            if position >= len(sequence):
                continue
            donor = donors[(parent_index + slot) % len(donors)]
            residue = donor.get("donor_residue") or donor["donor_fragment"][0]
            if residue == sequence[position]:
                continue
            child = sequence[:position] + residue + sequence[position + 1 :]
            child_hash = sha256_text(child)
            edit = (sequence, position, residue)
            if child_hash in seen or edit in edits:
                continue
            before, after = phi(sequence), phi(child)
            result.append(
                {
                    "branch_key": "pbp2a",
                    "target_key": "PBP2a",
                    "generation": generation,
                    "seed": seed,
                    "operator_id": "pbp2a-pepflow-same-domain-1aa-v1",
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
                    "donor_source": "PepFlow",
                    "donor_fragment": donor["donor_fragment"],
                    "source_proposal_id": donor.get(
                        "proposal_id", donor.get("donor_candidate_id", "")
                    ),
                    "sequence": child,
                    "sequence_sha256": child_hash,
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
                    "prior_edit_exclusion": "passed_parent_position_to_history_gate",
                    "matched_source_arms_excluded": True,
                }
            )
            seen.add(child_hash)
            edits.add(edit)
            if len(result) >= limit:
                return result
    return result


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
    if not parents:
        raise ValueError("parent CSV is empty")
    if len({row["run_id"] for row in parents}) != 1:
        raise ValueError("parents must come from one authoritative run")
    if any(not row.get("candidate_id", "") or len(row["candidate_id"]) != 36 for row in parents):
        raise ValueError("parent candidate IDs must be authoritative UUID strings")
    donors = select_pepflow_donors(read_csv(args.pepflow_csv))
    history, pg_counts = await pg_history(args.pg_url)
    edit_history = prior_edits(args.history_csv)
    proposals = generate(
        parents,
        donors,
        history_hashes=history,
        edits=edit_history,
        generation=args.generation,
        seed=args.seed,
        limit=args.limit,
    )
    if not proposals:
        raise ValueError("no PG-new proposal remained after exact history and edit exclusion")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "proposals.csv", proposals)
    receipt = {
        "schema_version": "ampgent.pbp2a-pepflow-same-domain-generation.1",
        "operator_id": "pbp2a-pepflow-same-domain-1aa-v1",
        "target_key": "PBP2a",
        "parent_run_id": parents[0]["run_id"],
        "parent_generation": int(parents[0].get("generation", "131")),
        "generation": args.generation,
        "seed": args.seed,
        "edit_positions_zero_based": list(EDIT_POSITIONS),
        "edit_budget": "one_residue_equal_length_substitution",
        "parent_count": len(parents),
        "pepflow_donor_residue_count": len(donors),
        "prior_edit_count": len(edit_history),
        "postgresql_history_counts": pg_counts,
        "postgresql_history_union_count": len(history),
        "proposal_count": len(proposals),
        "formal12_pending": True,
        "materialized_count": 0,
        "gpu_rosetta_md_submitted": False,
        "historical_run_modified": False,
    }
    (args.output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parents-csv", type=Path, required=True)
    parser.add_argument("--pepflow-csv", type=Path, required=True)
    parser.add_argument("--history-csv", type=Path, action="append", required=True)
    parser.add_argument("--pg-url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generation", type=int, default=132)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--limit", type=int, default=12)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
