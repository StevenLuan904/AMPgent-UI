"""Summarize exact Pool-A MD evidence into candidate and target reports."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean, median, pstdev

MD_RELEASE = "openmm_ff14sb_tip3p_1ns-npt_50ns-nvt_interface-pbc_v2"
MMGBSA_RELEASE = "ambertools26_mmgbsa_igb5_sparse_v1"

METRIC_SPECS = {
    "rosetta_median_dg_reu": ("REU", "lower"),
    "interface_rmsd_mean_nm": ("nm", "lower"),
    "interface_rmsd_max_nm": ("nm", "lower"),
    "native_contact_fraction_mean": ("fraction", "higher"),
    "native_contact_fraction_min": ("fraction", "higher"),
    "key_contact_occupancy_mean": ("fraction", "higher"),
    "key_contact_occupancy_max": ("fraction", "higher"),
    "hydrogen_bond_occupancy": ("fraction", "higher"),
    "salt_bridge_occupancy": ("fraction", "higher"),
    "water_bridge_occupancy": ("fraction", "higher"),
    "maximum_departure_duration_ps": ("ps", "lower"),
    "maximum_peptide_com_shift_nm": ("nm", "lower"),
    "mmgbsa_mean_kcal_mol": ("kcal/mol", "lower"),
    "mmgbsa_ci95_lower_kcal_mol": ("kcal/mol", "descriptive"),
    "mmgbsa_ci95_upper_kcal_mol": ("kcal/mol", "descriptive"),
}


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


def finite(value: object, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} is not finite")
    return result


def validated_interface_evidence(interface: dict | None) -> bool:
    if interface is None:
        return False
    if interface.get("schema_version") != "ampgent.pool-a-md-interface-analysis.2":
        raise ValueError("unexpected interface-analysis schema")
    for path in (
        ("interface_rmsd_nm", "mean"),
        ("interface_rmsd_nm", "maximum"),
        ("maximum_peptide_com_shift_nm",),
        ("maximum_departure_duration_ps",),
    ):
        if finite(nested(interface, *path), ".".join(path)) < 0:
            raise ValueError(f"{'.'.join(path)} is negative")
    for path in (
        ("native_contact_fraction", "mean"),
        ("native_contact_fraction", "minimum"),
        ("hydrogen_bond_occupancy",),
        ("salt_bridge_occupancy",),
        ("water_bridge_occupancy",),
    ):
        value = finite(nested(interface, *path), ".".join(path))
        if not 0 <= value <= 1:
            raise ValueError(f"{'.'.join(path)} is outside [0, 1]")
    for index, contact in enumerate(interface.get("key_contacts", [])):
        value = finite(contact.get("occupancy"), f"key_contacts[{index}].occupancy")
        if not 0 <= value <= 1:
            raise ValueError(f"key_contacts[{index}].occupancy is outside [0, 1]")
    if not isinstance(interface.get("peptide_departed"), bool):
        raise ValueError("peptide_departed is not boolean")
    return True


def validated_mmgbsa_evidence(mmgbsa: dict | None, decomposition: Path) -> bool:
    if mmgbsa is None:
        return False
    if mmgbsa.get("schema_version") != "ampgent.pool-a-mmgbsa.1":
        raise ValueError("unexpected MM/GBSA schema")
    if not decomposition.is_file():
        return False
    finite(mmgbsa.get("mean_binding_energy_kcal_mol"), "MM/GBSA mean")
    interval = mmgbsa.get("confidence_interval_95_kcal_mol")
    if not isinstance(interval, list) or len(interval) != 2:
        raise ValueError("MM/GBSA confidence interval is malformed")
    lower = finite(interval[0], "MM/GBSA confidence lower")
    upper = finite(interval[1], "MM/GBSA confidence upper")
    if lower > upper:
        raise ValueError("MM/GBSA confidence interval is reversed")
    if int(mmgbsa.get("frame_count", 0)) <= 0:
        raise ValueError("MM/GBSA frame count is not positive")
    declared = int(mmgbsa.get("decomposition_residue_count", 0))
    with decomposition.open(newline="", encoding="utf-8") as stream:
        observed = sum(1 for _ in csv.DictReader(stream))
    if declared <= 0 or observed != declared:
        raise ValueError(
            f"MM/GBSA decomposition row count mismatch: declared={declared}, observed={observed}"
        )
    return True


def validated_ingest_receipt(path: Path, expected: dict, release: str) -> bool:
    if not path.is_file():
        return False
    receipt = load(path)
    identity = {
        "candidate_id": expected["candidate_id"],
        "subject_run_id": expected["run_id"],
        "model_release_key": release,
    }
    for key, value in identity.items():
        if str(receipt.get(key)) != str(value):
            raise ValueError(
                f"PostgreSQL ingest receipt identity drift for {expected['candidate_id']}: {key}"
            )
    if not receipt.get("tool_call_id"):
        raise ValueError(
            f"PostgreSQL ingest receipt lacks tool_call_id for {expected['candidate_id']}"
        )
    return True


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
    interface_ingested = validated_ingest_receipt(interface_ingest, expected, MD_RELEASE)
    mmgbsa_ingested = validated_ingest_receipt(mmgbsa_ingest, expected, MMGBSA_RELEASE)
    md_complete = bool(
        manifest
        and manifest.get("status") == "succeeded"
        and float(manifest.get("npt_ns", 0)) == 1.0
        and float(manifest.get("production_ns", 0)) == 50.0
    )
    interface_complete = validated_interface_evidence(interface)
    mmgbsa_complete = validated_mmgbsa_evidence(mmgbsa, decomposition)
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
        "interface_postgresql_ingested": interface_ingested,
        "mmgbsa_postgresql_ingested": mmgbsa_ingested,
        "postgresql_evidence_complete": interface_ingested and mmgbsa_ingested,
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


def quantile(sorted_values: list[float], probability: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def metric_distribution(rows: list[dict], key: str, unit: str, direction: str) -> dict:
    observed = [(row, float(row[key])) for row in rows if row[key] is not None]
    values = sorted(value for _, value in observed)
    payload = {
        "observed_count": len(values),
        "missing_count": len(rows) - len(values),
        "unit": unit,
        "favorable_direction": direction,
    }
    if not values:
        return {
            **payload,
            "minimum": None,
            "maximum": None,
            "mean": None,
            "median": None,
            "population_standard_deviation": None,
            "p10": None,
            "p25": None,
            "p75": None,
            "p90": None,
            "best_candidate": None,
            "worst_candidate": None,
        }
    if direction == "higher":
        best_row, best_value = max(observed, key=lambda item: item[1])
        worst_row, worst_value = min(observed, key=lambda item: item[1])
    else:
        best_row, best_value = min(observed, key=lambda item: item[1])
        worst_row, worst_value = max(observed, key=lambda item: item[1])

    def identity(row: dict, value: float) -> dict:
        return {
            "candidate_id": row["candidate_id"],
            "run_id": row["run_id"],
            "sequence_sha256": row["sequence_sha256"],
            "value": value,
        }

    return {
        **payload,
        "minimum": values[0],
        "maximum": values[-1],
        "mean": fmean(values),
        "median": median(values),
        "population_standard_deviation": pstdev(values),
        "p10": quantile(values, 0.10),
        "p25": quantile(values, 0.25),
        "p75": quantile(values, 0.75),
        "p90": quantile(values, 0.90),
        "best_candidate": identity(best_row, best_value),
        "worst_candidate": identity(worst_row, worst_value),
    }


def aggregate(rows):
    departed_observed = [
        row["peptide_departed"]
        for row in rows
        if row["peptide_departed"] is not None
    ]
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
        "peptide_departure_categories": {
            "observed_count": len(departed_observed),
            "missing_count": len(rows) - len(departed_observed),
            "departed_count": sum(value is True for value in departed_observed),
            "retained_count": sum(value is False for value in departed_observed),
        },
        "metric_distributions": {
            key: metric_distribution(rows, key, unit, direction)
            for key, (unit, direction) in METRIC_SPECS.items()
        },
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
