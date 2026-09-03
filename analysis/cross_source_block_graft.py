"""Deterministic, bounded cross-source block graft proposals.

This operator only proposes candidates.  Safety, 12-metric scoring, calibration,
challenger, QD and PostgreSQL exact-history admission remain downstream gates.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pepagent.autoresearch_quality_diversity import behavior_vector
from pepagent.developability import sequence_developability_metrics
from pepagent.handoff_metrics import physicochemical_descriptors

AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
DEFAULT_BLOCK_LENGTHS = (3, 4, 5)
DONOR_SOURCE_TOKENS = ("pepglad", "pepmlm", "qd")


def _bool(row: dict[str, str], *keys: str) -> bool:
    return any(str(row.get(k, "")).strip().lower() in {"true", "1", "yes"} for k in keys)


def _support(row: dict[str, str]) -> int:
    for key in ("activity_model_support_count_calibrated", "activity_model_support_count"):
        value = row.get(key)
        if value not in (None, ""):
            try:
                return int(float(value))
            except ValueError:
                return 0
    return 0


def _family(row: dict[str, str]) -> str:
    return row.get("family_key_80_80") or row.get("source_family_key_80_80") or ""


def _source_label(row: dict[str, str]) -> str:
    return (row.get("source") or row.get("generator_id") or row.get("strategy") or "").strip()


def _is_donor_source(row: dict[str, str]) -> bool:
    text = " ".join(
        (row.get(key) or "")
        for key in ("source", "generator_id", "strategy", "proposal_mode", "qd_archive_status")
    ).lower()
    return any(token in text for token in DONOR_SOURCE_TOKENS) or _bool(
        row, "qd_elite", "qd_qualified"
    )


def _valid_sequence(row: dict[str, str]) -> bool:
    sequence = (row.get("sequence") or "").strip().upper()
    return 10 <= len(sequence) <= 30 and set(sequence) <= AMINO_ACIDS


def _instability_ok(row: dict[str, str]) -> bool:
    value = row.get("guruprasad_instability_index")
    try:
        return value not in (None, "") and float(value) <= 50.0
    except (TypeError, ValueError):
        return False


def _donor_hard_gates_ok(row: dict[str, str]) -> bool:
    return (
        _bool(row, "display_eligible")
        and (row.get("toxinpred3_label") or "").strip().lower() == "non-toxin"
        and (row.get("macrel_hemolysis_label") or "").strip().lower() == "low"
    )


def select_acceptors(rows: Iterable[dict[str, str]], branch: str = "acea") -> list[dict[str, str]]:
    selected = [
        row
        for row in rows
        if (row.get("target_key") or row.get("branch_key") or branch) == branch
        and _valid_sequence(row)
        and _bool(row, "display_eligible")
        and _support(row) == 0
        and _instability_ok(row)
    ]
    return sorted(
        selected, key=lambda r: ((r.get("family_key_80_80") or ""), r.get("sequence", ""))
    )


def select_donors(
    rows: Iterable[dict[str, str]],
    branch: str = "acea",
    excluded_families: set[str] | None = None,
) -> list[dict[str, str]]:
    excluded_families = excluded_families or set()
    selected = [
        row
        for row in rows
        if (row.get("target_key") or row.get("branch_key") or branch) == branch
        and _valid_sequence(row)
        and _donor_hard_gates_ok(row)
        and _support(row) >= 2
        and _is_donor_source(row)
        and _instability_ok(row)
        and _family(row)
        and _family(row) not in excluded_families
    ]
    return sorted(
        selected,
        key=lambda r: (-_support(r), _source_label(r), _family(r), r.get("sequence", "")),
    )


def _starts(length: int, block_length: int) -> list[int]:
    if length < block_length:
        return []
    return sorted({0, (length - block_length) // 2, length - block_length})


def _mapped_start(
    acceptor_start: int, acceptor_length: int, donor_length: int, block_length: int
) -> int:
    a_span = max(1, acceptor_length - block_length)
    d_span = max(0, donor_length - block_length)
    return min(d_span, round(acceptor_start * d_span / a_span))


def _formal_qd_coordinates(sequence: str) -> tuple[float, float, float, float]:
    metrics = sequence_developability_metrics(sequence)
    descriptors = physicochemical_descriptors(sequence, ph=7.4)
    vector = behavior_vector(
        sequence,
        net_charge=float(metrics["net_charge_ph7_4"]),
        hydrophobicity=float(descriptors["hydrophobic_ratio"]),
        hydrophobic_moment=float(descriptors["hydrophobic_moment"]),
    )
    return (
        vector.charge_density,
        vector.hydrophobicity,
        vector.hydrophobic_moment,
        float(vector.length),
    )


def _delta_phi(acceptor: str, donor: str, child: str) -> dict[str, Any]:
    # Transparent behavior-space coordinates; no hydrophobic threshold is applied.
    before = _formal_qd_coordinates(acceptor)
    after = _formal_qd_coordinates(child)
    return {
        "axes": ["net_charge_over_length", "hydrophobic_ratio", "hydrophobic_moment", "length"],
        "acceptor_to_child": [after[index] - before[index] for index in range(4)],
        "acceptor_length": len(acceptor),
        "donor_length": len(donor),
    }


def generate_grafts(
    acceptors: list[dict[str, str]],
    donors: list[dict[str, str]],
    *,
    max_pairs: int = 12,
    max_proposals_per_pair: int = 9,
    block_lengths: tuple[int, ...] = DEFAULT_BLOCK_LENGTHS,
    max_total_proposals: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    proposals: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    seen_children: set[str] = set()
    pair_count = 0
    for acceptor in acceptors:
        for donor in donors:
            if pair_count >= max_pairs:
                return proposals, actions
            if _family(acceptor) == _family(donor):
                continue
            pair_count += 1
            pair_proposals = 0
            for block_length in sorted(set(block_lengths)):
                for acceptor_start in _starts(len(acceptor["sequence"]), block_length):
                    if pair_proposals >= max_proposals_per_pair:
                        break
                    donor_start = _mapped_start(
                        acceptor_start,
                        len(acceptor["sequence"]),
                        len(donor["sequence"]),
                        block_length,
                    )
                    child = (
                        acceptor["sequence"][:acceptor_start]
                        + donor["sequence"][donor_start : donor_start + block_length]
                        + acceptor["sequence"][acceptor_start + block_length :]
                    )
                    if child == acceptor["sequence"] or not _valid_sequence({"sequence": child}):
                        continue
                    child_sha = hashlib.sha256(child.encode()).hexdigest()
                    if child_sha in seen_children:
                        continue
                    action = {
                        "operator_id": "cross-source-block-graft-v2-formal-descriptors",
                        "acceptor_candidate_id": acceptor.get("candidate_id")
                        or acceptor.get("sequence_sha256"),
                        "donor_candidate_id": donor.get("candidate_id")
                        or donor.get("sequence_sha256"),
                        "acceptor_family_key_80_80": _family(acceptor),
                        "donor_family_key_80_80": _family(donor),
                        "acceptor_source": _source_label(acceptor),
                        "donor_source": _source_label(donor),
                        "block_length": block_length,
                        "acceptor_start_zero_based": acceptor_start,
                        "donor_start_zero_based": donor_start,
                        "acceptor_block_sequence": acceptor["sequence"][
                            acceptor_start : acceptor_start + block_length
                        ],
                        "donor_block_sequence": donor["sequence"][
                            donor_start : donor_start + block_length
                        ],
                        "expected_improvement_axes": [
                            "cross_source_motif_transfer",
                            "activity_support",
                        ],
                    }
                    row = {
                        "sequence": child,
                        "sequence_sha256": child_sha,
                        "branch_key": "acea",
                        "proposal_mode": "cross-source-block-graft",
                        "parent_sequence": acceptor["sequence"],
                        "parent_candidate_id": action["acceptor_candidate_id"],
                        "acceptor_candidate_id": action["acceptor_candidate_id"],
                        "donor_candidate_id": action["donor_candidate_id"],
                        "acceptor_family_key_80_80": action["acceptor_family_key_80_80"],
                        "donor_family_key_80_80": action["donor_family_key_80_80"],
                        "acceptor_source": action["acceptor_source"],
                        "donor_source": action["donor_source"],
                        "block_length": block_length,
                        "acceptor_start_zero_based": acceptor_start,
                        "donor_start_zero_based": donor_start,
                        "acceptor_block_sequence": acceptor["sequence"][
                            acceptor_start : acceptor_start + block_length
                        ],
                        "donor_block_sequence": donor["sequence"][
                            donor_start : donor_start + block_length
                        ],
                        "delta_phi": json.dumps(
                            _delta_phi(acceptor["sequence"], donor["sequence"], child),
                            sort_keys=True,
                        ),
                        "guruprasad_precheck": "acceptor_and_donor_le_50",
                        "historical_exact_replay": "passed_postgresql_exact_history_gate",
                    }
                    proposals.append(row)
                    actions.append(action)
                    seen_children.add(child_sha)
                    pair_proposals += 1
                    if max_total_proposals and len(proposals) >= max_total_proposals:
                        return proposals, actions
                if pair_proposals >= max_proposals_per_pair:
                    break
    return proposals, actions


def _read_csv(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows.extend(csv.DictReader(stream))
    return rows


async def _historical_sequence_hashes() -> set[str]:
    # Keep the DB dependency lazy so the pure operator remains unit-testable.
    from autoresearch_safety_rescue_variants import _historical_sequence_sha256s

    return await _historical_sequence_sha256s()


def apply_exact_history_gate(
    proposals: list[dict[str, Any]], historical_hashes: set[str]
) -> list[dict[str, Any]]:
    return [row for row in proposals if row["sequence_sha256"] not in historical_hashes]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptor-csv", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-pairs", type=int, default=12)
    parser.add_argument("--max-proposals-per-pair", type=int, default=9)
    parser.add_argument("--max-total-proposals", type=int, default=12)
    parser.add_argument("--history-mode", choices=("postgresql",), default="postgresql")
    args = parser.parse_args()
    acceptors = select_acceptors(_read_csv([args.acceptor_csv]))
    donors = select_donors(
        _read_csv(args.donor_csv), excluded_families={_family(row) for row in acceptors}
    )
    proposals, actions = generate_grafts(
        acceptors,
        donors,
        max_pairs=args.max_pairs,
        max_proposals_per_pair=args.max_proposals_per_pair,
        max_total_proposals=args.max_total_proposals,
    )
    historical_hashes = asyncio.run(_historical_sequence_hashes())
    novel_proposals = apply_exact_history_gate(proposals, historical_hashes)
    novel_hashes = {row["sequence_sha256"] for row in novel_proposals}
    novel_actions = [
        action
        for action, proposal in zip(actions, proposals, strict=True)
        if proposal["sequence_sha256"] in novel_hashes
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if novel_proposals:
        with (args.output_dir / "proposals.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(novel_proposals[0]))
            writer.writeheader()
            writer.writerows(novel_proposals)
    receipt = {
        "schema_version": "ampgent.cross-source-block-graft.1",
        "operator_id": "cross-source-block-graft-v2-formal-descriptors",
        "acceptor_count": len(acceptors),
        "donor_count": len(donors),
        "pair_limit": args.max_pairs,
        "proposal_limit_per_pair": args.max_proposals_per_pair,
        "proposal_limit_total": args.max_total_proposals,
        "block_lengths": list(DEFAULT_BLOCK_LENGTHS),
        "proposal_count_before_history_gate": len(proposals),
        "proposal_count": len(novel_proposals),
        "action_count": len(novel_actions),
        "historical_sequence_exclusion_count": len(historical_hashes),
        "history_mode": args.history_mode,
        "downstream_gates": [
            "postgresql_exact_history",
            "toxinpred3_non_toxin",
            "macrel_low",
            "guruprasad_le_50",
            "score_all_12",
            "calibration",
            "hemopi2_challenger",
            "qd_2160",
        ],
        "gpu_md_interface_submitted": False,
    }
    (args.output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
