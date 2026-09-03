"""Summarize exact residue-pair contact occupancy for completed Pool-A MD."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean

from analysis.summarize_pool_a_residue_decomposition import AA3_TO_AA1

CONTACT_STABILITY_THRESHOLD = 0.5
TOP_CONTACT_COUNT = 10


def residue_label(label: object) -> tuple[str, int]:
    match = re.fullmatch(r"\s*([A-Za-z0-9]+?)(\d+)\s*", str(label))
    if match is None:
        raise ValueError(f"malformed residue label: {label!r}")
    return match.group(1).upper(), int(match.group(2))


def occupancy(value: object, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError(f"{label} is outside finite [0, 1]")
    return result


def candidate_contacts(candidate: dict, evidence_root: Path) -> dict | None:
    if candidate["interface_complete"] != "True":
        return None
    if candidate["interface_postgresql_ingested"] != "True":
        return None
    path = (
        evidence_root
        / candidate["target_key"]
        / candidate["candidate_id"]
        / "analysis/interface/interface_analysis.json"
    )
    analysis = json.loads(path.read_text(encoding="utf-8"))
    if analysis.get("schema_version") != "ampgent.pool-a-md-interface-analysis.2":
        raise ValueError(f"unexpected interface schema for {candidate['candidate_id']}")
    sequence = candidate["sequence"]
    contacts = []
    seen_pairs = set()
    for index, source in enumerate(analysis.get("key_contacts", [])):
        receptor_name, receptor_position = residue_label(source["receptor_residue"])
        peptide_name, peptide_position = residue_label(source["peptide_residue"])
        observed_aa = AA3_TO_AA1.get(peptide_name)
        if peptide_position < 1 or peptide_position > len(sequence):
            raise ValueError(
                f"peptide contact position outside sequence for {candidate['candidate_id']}"
            )
        expected_aa = sequence[peptide_position - 1]
        if observed_aa != expected_aa:
            raise ValueError(
                f"peptide contact identity mismatch for {candidate['candidate_id']} "
                f"at position {peptide_position}: {peptide_name} != {expected_aa}"
            )
        pair = (receptor_name, receptor_position, peptide_name, peptide_position)
        if pair in seen_pairs:
            raise ValueError(f"duplicate key contact for {candidate['candidate_id']}: {pair}")
        seen_pairs.add(pair)
        row = {
            "target_key": candidate["target_key"],
            "run_id": candidate["run_id"],
            "candidate_id": candidate["candidate_id"],
            "sequence": sequence,
            "sequence_sha256": candidate["sequence_sha256"],
            "receptor_residue": f"{receptor_name}{receptor_position}",
            "receptor_residue_name": receptor_name,
            "receptor_residue_position": receptor_position,
            "peptide_residue": f"{peptide_name}{peptide_position}",
            "peptide_residue_name": peptide_name,
            "peptide_residue_position": peptide_position,
            "occupancy": occupancy(
                source["occupancy"],
                f"{candidate['candidate_id']} key_contacts[{index}].occupancy",
            ),
        }
        contacts.append(row)
    contacts.sort(
        key=lambda row: (
            -row["occupancy"],
            row["receptor_residue_position"],
            row["peptide_residue_position"],
        )
    )
    stable = [row for row in contacts if row["occupancy"] >= CONTACT_STABILITY_THRESHOLD]
    return {
        "target_key": candidate["target_key"],
        "run_id": candidate["run_id"],
        "candidate_id": candidate["candidate_id"],
        "sequence": sequence,
        "sequence_sha256": candidate["sequence_sha256"],
        "key_contact_count": len(contacts),
        "stable_contact_count": len(stable),
        "top_contacts": contacts[:TOP_CONTACT_COUNT],
        "contacts": contacts,
    }


def target_hotspots(results: list[dict]) -> dict[str, list[dict]]:
    targets = defaultdict(list)
    for result in results:
        targets[result["target_key"]].append(result)
    output = {}
    for target_key, candidates in sorted(targets.items()):
        receptor_candidates = defaultdict(list)
        for candidate in candidates:
            candidate_maxima = {}
            for contact in candidate["contacts"]:
                residue = contact["receptor_residue"]
                candidate_maxima[residue] = max(
                    candidate_maxima.get(residue, 0.0), contact["occupancy"]
                )
            for residue, maximum in candidate_maxima.items():
                receptor_candidates[residue].append(maximum)
        hotspots = []
        for residue, values in receptor_candidates.items():
            name, position = residue_label(residue)
            hotspots.append(
                {
                    "receptor_residue": residue,
                    "receptor_residue_name": name,
                    "receptor_residue_position": position,
                    "completed_candidate_count": len(candidates),
                    "candidate_count_with_native_contact": len(values),
                    "candidate_prevalence": len(values) / len(candidates),
                    "mean_candidate_max_occupancy": fmean(values),
                    "maximum_candidate_occupancy": max(values),
                }
            )
        hotspots.sort(
            key=lambda row: (
                -row["candidate_prevalence"],
                -row["mean_candidate_max_occupancy"],
                row["receptor_residue_position"],
            )
        )
        output[target_key] = hotspots
    return output


def summarize(candidates: list[dict], evidence_root: Path) -> dict:
    complete = []
    for candidate in candidates:
        result = candidate_contacts(candidate, evidence_root)
        if result is not None:
            complete.append(result)
    all_contacts = [row for item in complete for row in item["contacts"]]
    return {
        "schema_version": "ampgent.pool-a-key-contact-occupancy.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "pool_a_candidate_count": len(candidates),
        "interface_and_postgresql_complete_count": len(complete),
        "pending_count": len(candidates) - len(complete),
        "key_contact_pair_count": len(all_contacts),
        "stable_contact_occupancy_threshold": CONTACT_STABILITY_THRESHOLD,
        "stable_contact_pair_count": sum(
            row["occupancy"] >= CONTACT_STABILITY_THRESHOLD for row in all_contacts
        ),
        "candidates": complete,
        "target_receptor_hotspots": target_hotspots(complete),
        "definitions": {
            "key_contact": "native residue pair closest-heavy <=0.45 nm",
            "candidate_prevalence": (
                "completed candidates whose native-contact set contains the receptor residue / "
                "completed candidates for that target"
            ),
        },
        "limitations": [
            "computed trajectory occupancy; not experimental binding evidence",
            "receptor hotspots are reported within target only and are not cross-target ranks",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contact-csv", type=Path, required=True)
    args = parser.parse_args()
    with args.candidates.open(newline="", encoding="utf-8") as stream:
        candidates = list(csv.DictReader(stream))
    payload = summarize(candidates, args.evidence_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    contacts = [row for item in payload["candidates"] for row in item["contacts"]]
    args.contact_csv.parent.mkdir(parents=True, exist_ok=True)
    temporary_csv = args.contact_csv.with_name(
        f".{args.contact_csv.name}.{os.getpid()}.tmp"
    )
    with temporary_csv.open("w", newline="", encoding="utf-8") as stream:
        fields = list(contacts[0]) if contacts else ["candidate_id"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(contacts)
    temporary_csv.replace(args.contact_csv)


if __name__ == "__main__":
    main()
