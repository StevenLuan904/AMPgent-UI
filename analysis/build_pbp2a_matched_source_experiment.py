"""Build a deterministic PepGLAD/PepFlow matched-source PBP2a batch.

The experiment keeps the parent, generation, seed, edit positions and edit
length fixed.  Only the residue donor changes between arms.  PostgreSQL is
read for authoritative parent identity and exact sequence history; this
module never materializes candidates or starts a workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import asyncpg

EDIT_POSITIONS = (0, 8, 15)
MAX_PER_ARM = 12
ARM_NAMES = ("PepGLAD", "PepFlow")
AA = set("ACDEFGHIKLMNPQRSTVWY")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _changed_edit(parent: str, child: str) -> tuple[str, int, str] | None:
    if not parent or len(parent) != len(child):
        return None
    changed = [
        index
        for index, pair in enumerate(zip(parent, child, strict=True))
        if pair[0] != pair[1]
    ]
    if not changed or changed != list(range(changed[0], changed[-1] + 1)):
        return None
    return parent, changed[0], child[changed[0] : changed[-1] + 1]


def load_prior_edits(paths: list[Path]) -> set[tuple[str, int, str]]:
    edits: set[tuple[str, int, str]] = set()
    for path in paths:
        for row in read_csv(path):
            edit = _changed_edit(
                (row.get("parent_sequence") or "").strip().upper(),
                (row.get("sequence") or "").strip().upper(),
            )
            if edit is not None:
                edits.add(edit)
    return edits


def validate_parent_rows(
    rows: list[dict[str, str]], *, run_id: str, parent_generation: int
) -> list[dict[str, str]]:
    if not rows:
        raise ValueError("parent set is empty")
    if not UUID_RE.fullmatch(run_id.lower()):
        raise ValueError("parent run_id must be a UUID")
    seen: set[str] = set()
    result = []
    for row in rows:
        candidate_id = row.get("candidate_id", "").strip().lower()
        sequence = row.get("sequence", "").strip().upper()
        if not UUID_RE.fullmatch(candidate_id):
            raise ValueError(f"parent candidate is not authoritative UUID: {candidate_id}")
        if candidate_id in seen or not sequence or any(residue not in AA for residue in sequence):
            raise ValueError("parent identity or sequence is invalid/duplicated")
        if int(row.get("generation", parent_generation)) != parent_generation:
            raise ValueError("parent generation drifted")
        expected_hash = sha256_text(sequence)
        if row.get("sequence_sha256", "").lower() != expected_hash:
            raise ValueError(f"parent sequence hash drifted: {candidate_id}")
        seen.add(candidate_id)
        result.append(
            {
                **row,
                "run_id": run_id,
                "candidate_id": candidate_id,
                "sequence": sequence,
                "sequence_sha256": expected_hash,
                "generation": str(parent_generation),
            }
        )
    return result


def select_donors(rows: list[dict[str, str]], *, source: str) -> list[dict[str, str]]:
    source_lower = source.lower()
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        row_source = (row.get("donor_source") or "").lower()
        if source_lower not in row_source:
            continue
        block = (row.get("donor_fragment") or row.get("fragment") or "").strip().upper()
        if not block or any(residue not in AA for residue in block):
            continue
        if block in seen:
            continue
        selected.append({**row, "donor_fragment": block, "donor_source": source})
        seen.add(block)
    if not selected:
        raise ValueError(f"no usable {source} donor fragments")
    return selected


def _residue_for_slot(block: str, parent_residue: str, slot: int) -> str | None:
    for offset in range(len(block)):
        residue = block[(slot + offset) % len(block)]
        if residue != parent_residue:
            return residue
    return None


def generate_arm(
    parents: list[dict[str, str]],
    donors: list[dict[str, str]],
    *,
    source: str,
    generation: int,
    seed: int,
    max_per_arm: int,
    history_hashes: set[str],
    prior_edits: set[tuple[str, int, str]],
) -> list[dict[str, Any]]:
    if max_per_arm < 1 or max_per_arm > MAX_PER_ARM:
        raise ValueError(f"max_per_arm must be in [1, {MAX_PER_ARM}]")
    result: list[dict[str, Any]] = []
    seen_hashes = set(history_hashes)
    for parent_index, parent in enumerate(parents):
        for slot, position in enumerate(EDIT_POSITIONS):
            if position >= len(parent["sequence"]):
                continue
            donor = donors[(parent_index + slot) % len(donors)]
            residue = _residue_for_slot(donor["donor_fragment"], parent["sequence"][position], slot)
            if residue is None:
                continue
            child = parent["sequence"][:position] + residue + parent["sequence"][position + 1 :]
            child_hash = sha256_text(child)
            edit = (parent["sequence"], position, residue)
            if child_hash in seen_hashes or edit in prior_edits:
                continue
            result.append(
                {
                    "source_arm": source,
                    "branch_key": "pbp2a",
                    "generation": generation,
                    "seed": seed,
                    "matched_pair_key": f"{parent_index}:{slot}:{position}",
                    "parent_run_id": parent["run_id"],
                    "parent_candidate_id": parent["candidate_id"],
                    "parent_sequence": parent["sequence"],
                    "parent_sequence_sha256": parent["sequence_sha256"],
                    "edit_position_1based": position + 1,
                    "edit_length": 1,
                    "edit": f"{parent['sequence'][position]}{position + 1}{residue}",
                    "sequence": child,
                    "sequence_sha256": child_hash,
                    "donor_candidate_id": donor.get("donor_candidate_id", ""),
                    "donor_source": source,
                    "donor_fragment": donor["donor_fragment"],
                    "donor_sequence_evidence": donor.get("donor_sequence", ""),
                    "operator_id": "pbp2a-matched-source-1aa-v1",
                    "historical_exact_replay": False,
                    "history_gate": "postgresql_candidate_and_operational_sequence_hashes",
                }
            )
            seen_hashes.add(child_hash)
            if len(result) >= max_per_arm:
                return result
    return result


async def postgres_history(pg_url: str) -> tuple[set[str], dict[str, int]]:
    conn = await asyncpg.connect(pg_url, timeout=5, command_timeout=15)
    try:
        candidate_rows = await conn.fetch(
            "select sequence_sha256 from candidates where sequence_sha256 is not null"
        )
        operational_rows = await conn.fetch(
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
        await conn.close()
    candidates = {str(row["sequence_sha256"]) for row in candidate_rows}
    operational = {
        str(row["sequence_sha256"])
        for row in operational_rows
        if row["sequence_sha256"]
    }
    invalid = {item for item in candidates | operational if len(item) != 64}
    if invalid:
        raise ValueError("PostgreSQL history contains malformed sequence hash")
    return candidates | operational, {
        "candidate_hash_count": len(candidates),
        "operational_hash_count": len(operational),
    }


async def verify_parents(pg_url: str, parents: list[dict[str, str]]) -> None:
    conn = await asyncpg.connect(pg_url, timeout=5, command_timeout=15)
    try:
        rows = await conn.fetch(
            """
            select c.id::text as candidate_id, c.run_id::text as run_id,
                   c.sequence, c.sequence_sha256, c.generation,
                   t.name as target_name, t.accession
            from candidates c
            join experiment_runs r on r.id = c.run_id
            join targets t on t.id = r.target_id
            where c.run_id = $1::uuid and c.id = any($2::uuid[])
            """,
            parents[0]["run_id"],
            [row["candidate_id"] for row in parents],
        )
    finally:
        await conn.close()
    observed = {row["candidate_id"]: dict(row) for row in rows}
    if len(observed) != len(parents):
        raise ValueError("authoritative parent candidate set is incomplete in PostgreSQL")
    for parent in parents:
        row = observed[parent["candidate_id"]]
        if row["run_id"] != parent["run_id"] or row["sequence"] != parent["sequence"]:
            raise ValueError("authoritative parent sequence/run binding drifted")
        if (
            row["sequence_sha256"] != parent["sequence_sha256"]
            or row["generation"] != int(parent["generation"])
        ):
            raise ValueError("authoritative parent hash/generation drifted")
        if "pbp2a" not in f"{row['target_name']} {row['accession']}".lower():
            raise ValueError("parent target is not PBP2a")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def run(args: argparse.Namespace) -> None:
    parent_rows = validate_parent_rows(
        read_csv(args.parents_csv),
        run_id=args.parent_run_id,
        parent_generation=args.parent_generation,
    )
    await verify_parents(args.pg_url, parent_rows)
    history_hashes, history_counts = await postgres_history(args.pg_url)
    donor_rows = {
        source: select_donors(read_csv(path), source=source)
        for source, path in (("PepGLAD", args.pepglad_csv), ("PepFlow", args.pepflow_csv))
    }
    prior_edits = load_prior_edits(args.history_csv)
    arms = {
        source: generate_arm(
            parent_rows,
            donor_rows[source],
            source=source,
            generation=args.generation,
            seed=args.seed,
            max_per_arm=args.max_per_arm,
            history_hashes=history_hashes,
            prior_edits=prior_edits,
        )
        for source in ARM_NAMES
    }
    if any(not rows for rows in arms.values()):
        raise ValueError("matched design has an empty source arm after exact history exclusion")
    pair_keys = {row["matched_pair_key"] for row in arms["PepGLAD"]} & {
        row["matched_pair_key"] for row in arms["PepFlow"]
    }
    combined = [row for source in ARM_NAMES for row in arms[source]]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "proposals.csv", combined)
    config = {
        "schema_version": "ampgent.pbp2a-matched-source.config.1",
        "operator_id": "pbp2a-matched-source-1aa-v1",
        "target_key": "pbp2a",
        "parent_run_id": args.parent_run_id,
        "parent_generation": args.parent_generation,
        "parent_candidate_ids": [row["candidate_id"] for row in parent_rows],
        "seed": args.seed,
        "edit_positions_zero_based": list(EDIT_POSITIONS),
        "edit_length": 1,
        "max_per_arm": args.max_per_arm,
        "arms": list(ARM_NAMES),
        "only_arm_variable": "donor_source_and_fragment",
        "historical_csvs": [str(path) for path in args.history_csv],
        "pg_history": history_counts,
        "gpu_rosetta_md_submitted": False,
    }
    _write_json(args.output_dir / "frozen_config.json", config)
    preflight = {
        "schema_version": "ampgent.pbp2a-matched-source.preflight.1",
        "status": "ready_for_formal_score_all",
        "parent_count": len(parent_rows),
        "parent_generation": args.parent_generation,
        "arm_counts": {source: len(arms[source]) for source in ARM_NAMES},
        "proposal_count": len(combined),
        "matched_pair_count": len(pair_keys),
        "pair_schedule_complete": len(pair_keys) == min(len(arms[source]) for source in ARM_NAMES),
        "donor_counts": {source: len(donor_rows[source]) for source in ARM_NAMES},
        "history_hash_count": len(history_hashes),
        "prior_edit_count": len(prior_edits),
        "pg_exact_replay_count": 0,
        "parent_identity": [
            {
                "run_id": row["run_id"],
                "candidate_id": row["candidate_id"],
                "sequence_sha256": row["sequence_sha256"],
            }
            for row in parent_rows
        ],
        "formal_score_all_complete": False,
        "materialized_count": 0,
        "rosetta_md_submitted": False,
    }
    _write_json(args.output_dir / "preflight.json", preflight)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parents-csv", type=Path, required=True)
    parser.add_argument("--parent-run-id", required=True)
    parser.add_argument("--parent-generation", type=int, required=True)
    parser.add_argument("--pepglad-csv", type=Path, required=True)
    parser.add_argument("--pepflow-csv", type=Path, required=True)
    parser.add_argument("--history-csv", type=Path, action="append", default=[])
    parser.add_argument("--pg-url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--max-per-arm", type=int, default=MAX_PER_ARM)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
