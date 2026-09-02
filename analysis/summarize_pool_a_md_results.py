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
    failure_path = candidate / "failure_receipt.json"
    interface_ingest = candidate / "analysis/interface/postgresql_ingest_receipt.json"
    mmgbsa_ingest = candidate / "analysis/mmgbsa/postgresql_ingest_receipt.json"
    launch = load(launch_path) if launch_path.is_file() else None
    if launch is not None:
        for key in ("candidate_id", "run_id", "target_key", "sequence_sha256"):
            if str(launch[key]) != str(expected[key]):
                raise ValueError(f"identity drift for {expected['candidate_id']}: {key}")
    manifest = load(manifest_path) if manifest_path.is_file() else None
    interface = load(interface_path) if interface_path.is_file() else None
    mmgbsa = load(mmgbsa_path) if mmgbsa_path.is_file() else None
    failure = load(failure_path) if failure_path.is_file() else None
    md_complete = bool(
        manifest
        and manifest.get("status") == "succeeded"
        and float(manifest.get("npt_ns", 0)) == 1.0
        and float(manifest.get("production_ns", 0)) == 50.0
    )
    interface_complete = bool(
        interface
        and interface.get("schema_version") == "ampgent.pool-a-md-interface-analysis.2"
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
        "retry_failure_recorded": failure is not None,
        "last_attempt_returncode": nested(failure, "returncode"),
        "last_attempt_will_retry": nested(failure, "will_retry"),
        "interface_postgresql_ingested": interface_ingest.is_file(),
        "mmgbsa_postgresql_ingested": mmgbsa_ingest.is_file(),
        "postgresql_evidence_complete": interface_ingest.is_file() and mmgbsa_ingest.is_file(),
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
        "pending_md_count": sum(not row["md_complete"] for row in rows),
        "pending_pool_s_evidence_count": sum(
            not row["pool_s_evidence_complete"] for row in rows
        ),
        "retry_failure_recorded_count": sum(row["retry_failure_recorded"] for row in rows),
        "interface_postgresql_ingested_count": sum(
            row["interface_postgresql_ingested"] for row in rows
        ),
        "mmgbsa_postgresql_ingested_count": sum(
            row["mmgbsa_postgresql_ingested"] for row in rows
        ),
        "postgresql_evidence_complete_count": sum(
            row["postgresql_evidence_complete"] for row in rows
        ),
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
    candidate_report = output / "candidates.csv"
    candidate_temporary = output / ".candidates.csv.tmp"
    with candidate_temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    candidate_temporary.replace(candidate_report)
    targets = defaultdict(list)
    for row in rows:
        targets[row["target_key"]].append(row)
    overall = aggregate(rows)
    expected_count = overall["expected_candidate_count"]
    complete = expected_count > 0 and all(
        overall[key] == expected_count
        for key in (
            "md_complete_count",
            "interface_complete_count",
            "mmgbsa_complete_count",
            "postgresql_evidence_complete_count",
        )
    )
    completion_report = output / "completion_receipt.json"
    payload = {
        "schema_version": "ampgent.pool-a-md-summary.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "overall": overall,
        "targets": {key: aggregate(value) for key, value in sorted(targets.items())},
        "candidate_report": str(candidate_report.resolve()),
        "completion_receipt": str(completion_report.resolve()) if complete else None,
    }
    summary_report = output / "summary.json"
    summary_temporary = output / ".summary.json.tmp"
    summary_temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    summary_temporary.replace(summary_report)
    if complete and not completion_report.exists():
        completion = {
            "schema_version": "ampgent.pool-a-md-completion.1",
            "status": "succeeded",
            "completed_at_utc": datetime.now(UTC).isoformat(),
            "snapshot": str(snapshot.resolve()),
            "evidence_root": str(evidence_root.resolve()),
            "expected_candidate_count": expected_count,
            "md_complete_count": overall["md_complete_count"],
            "interface_complete_count": overall["interface_complete_count"],
            "mmgbsa_complete_count": overall["mmgbsa_complete_count"],
            "postgresql_evidence_complete_count": overall[
                "postgresql_evidence_complete_count"
            ],
            "structure_or_trajectory_downloaded": False,
            "remote_files_deleted": False,
        }
        completion_temporary = output / ".completion_receipt.json.tmp"
        completion_temporary.write_text(
            json.dumps(completion, indent=2) + "\n", encoding="utf-8"
        )
        completion_temporary.replace(completion_report)
    return payload


def main():
    args = cli()
    summarize(args.snapshot, args.evidence_root, args.output_dir)


if __name__ == "__main__":
    main()
