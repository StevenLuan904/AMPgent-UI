"""Build exact, compact Pool-S dossiers from completed Pool-A MD evidence."""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from analysis.analyze_pool_s_frontier import candidate_payload, typed

IDENTITY = ("target_key", "run_id", "candidate_id", "sequence", "sequence_sha256")


def key(row: dict) -> tuple[str, str]:
    return str(row["run_id"]), str(row["candidate_id"])


def exact_index(rows: list[dict], label: str) -> dict[tuple[str, str], dict]:
    result = {}
    for row in rows:
        identity = key(row)
        if identity in result:
            raise ValueError(f"duplicate {label} identity: {identity}")
        result[identity] = row
    return result


def verify_identity(expected: dict, observed: dict, label: str) -> None:
    for field in IDENTITY:
        if str(observed[field]) != str(expected[field]):
            raise ValueError(
                f"{label} identity drift for {expected['candidate_id']}: {field}"
            )


def build(
    rows: list[dict], contacts_payload: dict, decomposition_payload: dict, frontier: dict
) -> dict:
    complete = [
        row
        for row in rows
        if row["pool_s_evidence_complete"] and row["postgresql_evidence_complete"]
    ]
    contacts = exact_index(contacts_payload["candidates"], "contact")
    decomposition = exact_index(decomposition_payload["candidates"], "decomposition")
    frontier_ids = {
        key(item)
        for target in frontier["targets"].values()
        for item in target["provisional_frontier"]
    }
    complete_ids = {key(row) for row in complete}
    if set(contacts) != complete_ids:
        raise ValueError("contact dossier coverage does not match completed candidates")
    if set(decomposition) != complete_ids:
        raise ValueError("decomposition dossier coverage does not match completed candidates")
    if not frontier_ids <= complete_ids:
        raise ValueError("provisional frontier contains an incomplete candidate")
    dossiers = []
    for row in complete:
        identity = key(row)
        contact = contacts[identity]
        residue = decomposition[identity]
        verify_identity(row, contact, "contact")
        verify_identity(row, residue, "decomposition")
        supporting = candidate_payload(row)
        dossiers.append(
            {
                **{field: row[field] for field in IDENTITY},
                "pool_a_rank": row["pool_a_rank"],
                "rosetta_median_dg_reu": row["rosetta_median_dg_reu"],
                "provisional_pool_s_frontier_member": identity in frontier_ids,
                "interface": {
                    "rmsd_mean_nm": row["interface_rmsd_mean_nm"],
                    "rmsd_maximum_nm": row["interface_rmsd_max_nm"],
                    "native_contact_fraction_mean": row[
                        "native_contact_fraction_mean"
                    ],
                    "native_contact_fraction_minimum": row[
                        "native_contact_fraction_min"
                    ],
                    "key_contact_count": row["key_contact_count"],
                    "key_contact_occupancy_mean": row[
                        "key_contact_occupancy_mean"
                    ],
                    "key_contact_occupancy_maximum": row[
                        "key_contact_occupancy_max"
                    ],
                    "top_key_contacts": contact["top_contacts"],
                },
                "interaction_occupancy": {
                    "hydrogen_bond": row["hydrogen_bond_occupancy"],
                    "salt_bridge": row["salt_bridge_occupancy"],
                    "water_bridge": row["water_bridge_occupancy"],
                },
                "departure": {
                    "peptide_departed": row["peptide_departed"],
                    "maximum_duration_ps": row["maximum_departure_duration_ps"],
                    "maximum_com_shift_nm": row["maximum_peptide_com_shift_nm"],
                },
                "mmgbsa": {
                    "mean_binding_energy_kcal_mol": row["mmgbsa_mean_kcal_mol"],
                    "confidence_interval_95_kcal_mol": [
                        row["mmgbsa_ci95_lower_kcal_mol"],
                        row["mmgbsa_ci95_upper_kcal_mol"],
                    ],
                    "frame_count": row["mmgbsa_frame_count"],
                    "peptide_total_decomposition_kcal_mol": residue[
                        "peptide_total_decomposition_kcal_mol"
                    ],
                    "top_favorable_peptide_residues": residue[
                        "top_favorable_residues"
                    ],
                    "top_unfavorable_peptide_residues": residue[
                        "top_unfavorable_residues"
                    ],
                },
                "source_candidate_summary": supporting,
            }
        )
    dossiers.sort(key=lambda row: (row["target_key"], row["pool_a_rank"]))
    return {
        "schema_version": "ampgent.pool-s-candidate-dossiers.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "pool_a_candidate_count": len(rows),
        "complete_dossier_count": len(dossiers),
        "pending_dossier_count": len(rows) - len(dossiers),
        "provisional_pool_s_frontier_count": len(frontier_ids),
        "dossiers": dossiers,
        "limitations": [
            "computed MD, Rosetta and MM/GBSA evidence; not experimental activity or affinity",
            "Pool-S frontier membership is target-local and provisional until all MD completes",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--contacts", type=Path, required=True)
    parser.add_argument("--decomposition", type=Path, required=True)
    parser.add_argument("--frontier", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.candidates.open(newline="", encoding="utf-8") as stream:
        rows = [typed(row) for row in csv.DictReader(stream)]
    payload = build(
        rows,
        json.loads(args.contacts.read_text(encoding="utf-8")),
        json.loads(args.decomposition.read_text(encoding="utf-8")),
        json.loads(args.frontier.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
