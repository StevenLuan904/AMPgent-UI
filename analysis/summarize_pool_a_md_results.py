"""Summarize exact Pool-A MD evidence into candidate and target reports."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def nested(data, *keys):
    for key in keys:
        if data is None:
            return None
        data = data.get(key)
    return data


def candidate_row(expected: dict, root: Path) -> dict:
    candidate = root / expected["target_key"] / expected["candidate_id"]
    launch_path = candidate / "launch_receipt.json"
    manifest_path = candidate / "manifest.json"
    interface_path = candidate / "analysis/interface/interface_analysis.json"
    mmgbsa_path = candidate / "analysis/mmgbsa/mmgbsa_analysis.json"
    decomposition = candidate / "analysis/mmgbsa/residue_decomposition_mean.csv"
    launch = load(launch_path) if launch_path.is_file() else None
    if launch is not None:
        for key in ("candidate_id", "run_id", "target_key", "sequence_sha256"):
            if str(launch[key]) != str(expected[key]):
                raise ValueError(f"identity drift for {expected['candidate_id']}: {key}")
    manifest = load(manifest_path) if manifest_path.is_file() else None
    interface = load(interface_path) if interface_path.is_file() else None
    mmgbsa = load(mmgbsa_path) if mmgbsa_path.is_file() else None
    md_complete = bool(
        manifest
        and manifest.get("status") == "succeeded"
        and float(manifest.get("npt_ns", 0)) == 1.0
        and float(manifest.get("production_ns", 0)) == 50.0
    )
    interface_complete = bool(
        interface
        and interface.get("schema_version") == "ampgent.pool-a-md-interface-analysis.1"
    )
    mmgbsa_complete = bool(
        mmgbsa
        and mmgbsa.get("schema_version") == "ampgent.pool-a-mmgbsa.1"
        and decomposition.is_file()
    )
    contacts = interface.get("key_contacts", []) if interface else []
    occupancies = [float(item["occupancy"]) for item in contacts]
    return {
        "target_key": expected["target_key"],
        "candidate_id": expected["candidate_id"],
        "run_id": expected["run_id"],
        "sequence_sha256": expected["sequence_sha256"],
        "pool_a_rank": expected["pool_a_rank"],
        "rosetta_median_dg_reu": expected["primary_dg"],
        "md_launched": launch is not None,
        "md_complete": md_complete,
        "interface_complete": interface_complete,
        "mmgbsa_complete": mmgbsa_complete,
        "pool_s_evidence_complete": md_complete and interface_complete and mmgbsa_complete,
        "interface_rmsd_mean_nm": nested(interface, "interface_rmsd_nm", "mean"),
        "interface_rmsd_max_nm": nested(interface, "interface_rmsd_nm", "maximum"),
        "native_contact_fraction_mean": nested(interface, "native_contact_fraction", "mean"),
        "native_contact_fraction_min": nested(interface, "native_contact_fraction", "minimum"),
        "key_contact_count": len(contacts) if interface else None,
        "key_contact_occupancy_mean": fmean(occupancies) if occupancies else None,
        "key_contact_occupancy_max": max(occupancies) if occupancies else None,
        "hydrogen_bond_occupancy": nested(interface, "hydrogen_bond_occupancy"),
        "salt_bridge_occupancy": nested(interface, "salt_bridge_occupancy"),
        "water_bridge_occupancy": nested(interface, "water_bridge_occupancy"),
        "peptide_departed": nested(interface, "peptide_departed"),
        "maximum_departure_duration_ps": nested(interface, "maximum_departure_duration_ps"),
        "maximum_peptide_com_shift_nm": nested(interface, "maximum_peptide_com_shift_nm"),
        "mmgbsa_mean_kcal_mol": nested(mmgbsa, "mean_binding_energy_kcal_mol"),
        "mmgbsa_ci95_lower_kcal_mol": (
            mmgbsa["confidence_interval_95_kcal_mol"][0] if mmgbsa else None
        ),
        "mmgbsa_ci95_upper_kcal_mol": (
            mmgbsa["confidence_interval_95_kcal_mol"][1] if mmgbsa else None
        ),
        "mmgbsa_frame_count": nested(mmgbsa, "frame_count"),
        "decomposition_residue_count": nested(mmgbsa, "decomposition_residue_count"),
    }


def mean(rows, key):
    values = [float(row[key]) for row in rows if row[key] is not None]
    return fmean(values) if values else None


def aggregate(rows):
    return {
        "expected_candidate_count": len(rows),
        "md_launched_count": sum(row["md_launched"] for row in rows),
        "md_complete_count": sum(row["md_complete"] for row in rows),
        "interface_complete_count": sum(row["interface_complete"] for row in rows),
        "mmgbsa_complete_count": sum(row["mmgbsa_complete"] for row in rows),
        "pool_s_evidence_complete_count": sum(row["pool_s_evidence_complete"] for row in rows),
        "peptide_departed_count": sum(row["peptide_departed"] is True for row in rows),
        "interface_rmsd_mean_nm": mean(rows, "interface_rmsd_mean_nm"),
        "native_contact_fraction_mean": mean(rows, "native_contact_fraction_mean"),
        "hydrogen_bond_occupancy_mean": mean(rows, "hydrogen_bond_occupancy"),
        "salt_bridge_occupancy_mean": mean(rows, "salt_bridge_occupancy"),
        "water_bridge_occupancy_mean": mean(rows, "water_bridge_occupancy"),
        "mmgbsa_mean_kcal_mol": mean(rows, "mmgbsa_mean_kcal_mol"),
    }


def summarize(snapshot: Path, evidence_root: Path, output: Path) -> dict:
    expected = load(snapshot)["pool_a_all"]
    rows = [candidate_row(item, evidence_root) for item in expected]
    output.mkdir(parents=True, exist_ok=True)
    with (output / "candidates.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    targets = defaultdict(list)
    for row in rows:
        targets[row["target_key"]].append(row)
    payload = {
        "schema_version": "ampgent.pool-a-md-summary.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "overall": aggregate(rows),
        "targets": {key: aggregate(value) for key, value in sorted(targets.items())},
        "candidate_report": str((output / "candidates.csv").resolve()),
    }
    (output / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main():
    args = cli()
    summarize(args.snapshot, args.evidence_root, args.output_dir)


if __name__ == "__main__":
    main()
