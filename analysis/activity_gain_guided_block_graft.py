"""Bounded activity-endpoint-gain-guided block graft operator."""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from cross_source_block_graft import (
    _delta_phi,
    _family,
    _historical_sequence_hashes,
    _valid_sequence,
    select_acceptors,
    select_donors,
)

ENDPOINTS = (
    ("llamp", "llamp_log10_mic_um__parent_benefit_percentile"),
    ("amp_read", "amp_read_log10_mic_um__parent_benefit_percentile"),
    ("macrel", "macrel_amp_probability__parent_benefit_percentile"),
)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def diagnose_endpoint_gains(
    scored_rows: list[dict[str, str]], donor_rows: list[dict[str, str]]
) -> list[dict[str, Any]]:
    donors = {
        row.get("candidate_id", ""): row
        for row in select_donors(donor_rows, excluded_families=set())
    }
    diagnostic: list[dict[str, Any]] = []
    for row in scored_rows:
        donor_id = row.get("donor_candidate_id", "")
        donor = donors.get(donor_id)
        if donor is None:
            continue
        gains = [name for name, column in ENDPOINTS if float(row[column]) > 0.0]
        if not gains:
            continue
        diagnostic.append(
            {
                "donor_candidate_id": donor_id,
                "donor_sequence": donor["sequence"],
                "donor_family_key_80_80": _family(donor),
                "donor_source": donor.get("generator_id") or donor.get("source", ""),
                "donor_activity_support": int(float(donor.get("activity_model_support_count", 0))),
                "donor_block_sequence": row["donor_block_sequence"],
                "block_length": int(row["block_length"]),
                "llamp_gain": float(row[ENDPOINTS[0][1]]),
                "amp_read_gain": float(row[ENDPOINTS[1][1]]),
                "macrel_gain": float(row[ENDPOINTS[2][1]]),
                "independent_gain_axes": gains,
                "prior_child_sequence": row["sequence"],
                "prior_acceptor_start_zero_based": int(row["acceptor_start_zero_based"]),
            }
        )
    return sorted(
        diagnostic,
        key=lambda row: (
            row["donor_candidate_id"],
            -len(row["independent_gain_axes"]),
            -max(row["llamp_gain"], row["amp_read_gain"], row["macrel_gain"]),
            row["donor_block_sequence"],
        ),
    )


def generate_guided(
    acceptors: list[dict[str, str]],
    diagnostic: list[dict[str, Any]],
    *,
    max_total: int = 24,
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    seen: set[str] = set()
    for acceptor in acceptors:
        sequence = acceptor["sequence"]
        for fragment in diagnostic:
            block = fragment["donor_block_sequence"]
            length = len(block)
            if not 3 <= length <= 5 or len(sequence) < length:
                continue
            for start in range(len(sequence) - length + 1):
                if start == fragment["prior_acceptor_start_zero_based"]:
                    continue
                child = sequence[:start] + block + sequence[start + length :]
                if child == sequence or not _valid_sequence({"sequence": child}):
                    continue
                child_sha = hashlib.sha256(child.encode()).hexdigest()
                if child_sha in seen:
                    continue
                action = {
                    "operator_id": "activity-gain-guided-block-graft-v1",
                    "acceptor_candidate_id": acceptor.get("candidate_id") or acceptor.get("sequence_sha256"),
                    "donor_candidate_id": fragment["donor_candidate_id"],
                    "independent_gain_axes": fragment["independent_gain_axes"],
                    "source_donor_block": block,
                    "acceptor_start_zero_based": start,
                }
                proposals.append(
                    {
                        "sequence": child,
                        "sequence_sha256": child_sha,
                        "branch_key": "acea",
                        "proposal_mode": "activity-gain-guided-block-graft",
                        "parent_sequence": sequence,
                        "parent_candidate_id": action["acceptor_candidate_id"],
                        "acceptor_candidate_id": action["acceptor_candidate_id"],
                        "donor_candidate_id": fragment["donor_candidate_id"],
                        "acceptor_family_key_80_80": _family(acceptor),
                        "donor_family_key_80_80": fragment["donor_family_key_80_80"],
                        "acceptor_source": acceptor.get("generator_id") or acceptor.get("source", ""),
                        "donor_source": fragment["donor_source"],
                        "block_length": length,
                        "acceptor_start_zero_based": start,
                        "donor_block_sequence": block,
                        "independent_gain_axes": json.dumps(fragment["independent_gain_axes"]),
                        "endpoint_gain_llamp": fragment["llamp_gain"],
                        "endpoint_gain_amp_read": fragment["amp_read_gain"],
                        "endpoint_gain_macrel": fragment["macrel_gain"],
                        "delta_phi": json.dumps(_delta_phi(sequence, fragment["donor_sequence"], child), sort_keys=True),
                        "guruprasad_precheck": "acceptor_and_donor_le_50",
                        "historical_exact_replay": "pending_postgresql_exact_history_gate",
                    }
                )
                seen.add(child_sha)
                if len(proposals) >= max_total:
                    return proposals
    return proposals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-scores", type=Path, required=True)
    parser.add_argument("--acceptor-csv", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-total", type=int, default=24)
    args = parser.parse_args()
    scored = _rows(args.parent_scores)
    acceptors = select_acceptors(_rows(args.acceptor_csv))
    diagnostic = diagnose_endpoint_gains(scored, _rows(args.donor_csv))
    proposals = generate_guided(acceptors, diagnostic, max_total=args.max_total)
    history = asyncio.run(_historical_sequence_hashes())
    novel = [row for row in proposals if row["sequence_sha256"] not in history]
    for row in novel:
        row["historical_exact_replay"] = "passed_postgresql_exact_history_gate"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if diagnostic:
        with (args.output_dir / "endpoint_gain_diagnostic.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(diagnostic[0]))
            writer.writeheader(); writer.writerows(diagnostic)
    if novel:
        with (args.output_dir / "proposals.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(novel[0]))
            writer.writeheader(); writer.writerows(novel)
    (args.output_dir / "selection_receipt.json").write_text(
        json.dumps({
            "schema_version": "ampgent.activity-gain-guided-block-graft.1",
            "operator_id": "activity-gain-guided-block-graft-v1",
            "acceptor_count": len(acceptors),
            "diagnostic_fragment_count": len(diagnostic),
            "proposal_count_before_history_gate": len(proposals),
            "proposal_count": len(novel),
            "historical_sequence_exclusion_count": len(history),
            "max_total": args.max_total,
            "downstream_gates": ["score_all_12", "calibration", "hemopi2_apex_peptiverse_shadow", "qd_2160"],
            "gpu_md_rosetta_submitted": False,
        }, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
