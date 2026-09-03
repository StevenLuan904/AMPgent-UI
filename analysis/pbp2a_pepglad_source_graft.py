"""Bounded PepGLAD-fragment grafts for a PBP2a activity-supported archive.

The PepGLAD rows are fragment evidence from the frozen strict-library-derived
factorized PepGLAD outcome, not admission-qualified donors.  Admission is
performed on the final child by the downstream safety, score-all, calibration,
challenger and QD stages.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from autoresearch_safety_rescue_variants import _historical_sequence_sha256s
from cross_source_block_graft import _delta_phi, _valid_sequence


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _as_bool(row: dict[str, str], key: str) -> bool:
    return str(row.get(key, "")).strip().lower() in {"true", "1", "yes"}


def _support(row: dict[str, str]) -> int:
    return int(float(row.get("activity_model_support_count_calibrated") or 0))


def select_archive_parents(
    score_rows: list[dict[str, str]], archive_payload: dict[str, Any], *, limit: int = 4
) -> list[dict[str, str]]:
    elites = archive_payload["branches"]["pbp2a"]["elites"]
    elite_by_sequence = {str(item["sequence"]).upper(): item for item in elites}
    candidates = []
    for row in score_rows:
        sequence = row.get("sequence", "").upper()
        elite = elite_by_sequence.get(sequence)
        if elite is None:
            continue
        if not (
            _as_bool(row, "display_eligible")
            and _as_bool(row, "formal_12_complete")
            and _support(row) >= 2
            and float(row.get("guruprasad_instability_index") or 999) <= 50
        ):
            continue
        family = row.get("family_key_80_80") or row.get("source_family_key_80_80")
        if not family:
            continue
        candidates.append(
            {
                **row,
                "archive_cell_id": str(elite["cell_id"]),
                "archive_quality": str(elite["quality"]),
                "archive_candidate_id": str(elite["candidate_id"]),
                "archive_family_key_80_80": family,
            }
        )
    selected: list[dict[str, str]] = []
    seen_families: set[str] = set()
    seen_cells: set[str] = set()
    for row in sorted(
        candidates,
        key=lambda item: (
            -float(item["archive_quality"]),
            item["archive_cell_id"],
            item["sequence"],
        ),
    ):
        family = row["archive_family_key_80_80"]
        cell = row["archive_cell_id"]
        if family in seen_families or cell in seen_cells:
            continue
        selected.append(row)
        seen_families.add(family)
        seen_cells.add(cell)
        if len(selected) >= limit:
            break
    return selected


def select_pepglad_fragments(
    rows: list[dict[str, str]], *, limit: int = 3
) -> list[dict[str, str]]:
    usable = []
    for row in rows:
        source = (row.get("donor_source") or "").lower()
        fragment = (row.get("donor_block_sequence") or "").upper()
        if "pepglad" not in source or not 1 <= len(fragment) <= 5:
            continue
        if not fragment or any(residue not in "ACDEFGHIKLMNPQRSTVWY" for residue in fragment):
            continue
        gains = row.get("independent_gain_axes", "")
        try:
            gain_count = len(json.loads(gains.replace("'", '"')))
        except (TypeError, ValueError, json.JSONDecodeError):
            gain_count = 0
        usable.append((gain_count, fragment, row))
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    for _, fragment, row in sorted(usable, key=lambda item: (-item[0], item[1])):
        if fragment in seen:
            continue
        selected.append({**row, "fragment": fragment, "donor_source": "PepGLAD"})
        seen.add(fragment)
        if len(selected) >= limit:
            break
    return selected


def _infer_edit(row: dict[str, str]) -> tuple[str, int, str] | None:
    parent = (row.get("parent_sequence") or "").upper()
    child = (row.get("sequence") or "").upper()
    if not parent or len(parent) != len(child):
        return None
    changed = [
        index
        for index, (before, after) in enumerate(zip(parent, child, strict=True))
        if before != after
    ]
    if not changed or changed != list(range(changed[0], changed[-1] + 1)):
        return None
    start = changed[0]
    return parent, start, child[start : changed[-1] + 1]


def load_prior_edits(paths: list[Path]) -> set[tuple[str, int, str]]:
    edits: set[tuple[str, int, str]] = set()
    for path in paths:
        try:
            rows = _read_csv(path)
        except (OSError, UnicodeError, csv.Error):
            continue
        for row in rows:
            if (row.get("branch_key") or "").strip().lower() != "pbp2a":
                continue
            edit = _infer_edit(row)
            if edit is not None:
                edits.add(edit)
    return edits


def generate_proposals(
    parents: list[dict[str, str]],
    fragments: list[dict[str, str]],
    *,
    prior_edits: set[tuple[str, int, str]] | None = None,
    history_hashes: set[str] | None = None,
    generation: int = 1,
    max_total: int = 12,
) -> list[dict[str, Any]]:
    if generation < 1:
        raise ValueError("generation must be positive")
    prior_edits = prior_edits or set()
    history_hashes = history_hashes or set()
    proposals: list[dict[str, Any]] = []
    seen_hashes: set[str] = set(history_hashes)
    for parent_index, parent in enumerate(parents):
        sequence = parent["sequence"].upper()
        for fragment_index, fragment_row in enumerate(fragments):
            fragment = fragment_row["fragment"]
            positions = sorted(
                {0, (len(sequence) - len(fragment)) // 2, len(sequence) - len(fragment)}
            )
            position_index = (parent_index + fragment_index) % len(positions)
            options = positions[position_index:] + positions[:position_index]
            chosen = None
            for candidate_position in options:
                child = (
                    sequence[:candidate_position]
                    + fragment
                    + sequence[candidate_position + len(fragment) :]
                )
                edit = (sequence, candidate_position, fragment)
                child_hash = hashlib.sha256(child.encode()).hexdigest()
                if child == sequence or child_hash in seen_hashes or edit in prior_edits:
                    continue
                if not _valid_sequence({"sequence": child}):
                    continue
                chosen = (candidate_position, child, child_hash)
                break
            if chosen is None:
                continue
            candidate_position, child, child_hash = chosen
            proposals.append(
                {
                    "sequence": child,
                    "sequence_sha256": child_hash,
                    "branch_key": "pbp2a",
                    "generation": generation,
                    "proposal_mode": "pbp2a-pepglad-source-graft-v1",
                    "parent_sequence": sequence,
                    "parent_sequence_sha256": hashlib.sha256(
                        sequence.encode("utf-8")
                    ).hexdigest(),
                    "parent_candidate_id": parent.get("candidate_id", ""),
                    "parent_archive_candidate_id": parent["archive_candidate_id"],
                    "parent_archive_cell_id": parent["archive_cell_id"],
                    "parent_archive_family_key_80_80": parent["archive_family_key_80_80"],
                    "parent_archive_quality": parent["archive_quality"],
                    "donor_candidate_id": fragment_row.get("donor_candidate_id", ""),
                    "donor_source": "PepGLAD",
                    "donor_fragment": fragment,
                    "donor_sequence_evidence": fragment_row.get("donor_sequence", ""),
                    "donor_family_key_80_80": fragment_row.get("donor_family_key_80_80", ""),
                    "graft_start_zero_based": candidate_position,
                    "graft_length": len(fragment),
                    "operator_id": "pbp2a-pepglad-source-graft-v1",
                    "delta_phi": json.dumps(
                        _delta_phi(sequence, fragment_row.get("donor_sequence", fragment), child),
                        sort_keys=True,
                    ),
                    "historical_exact_replay": "passed_postgresql_exact_history_gate",
                }
            )
            seen_hashes.add(child_hash)
            if len(proposals) >= max_total:
                return proposals
    return proposals


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write empty proposal CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-scores", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--pepglad-fragments", type=Path, required=True)
    parser.add_argument("--history-csv", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generation", type=int, default=1)
    parser.add_argument("--max-total", type=int, default=12)
    args = parser.parse_args()
    parents = select_archive_parents(
        _read_csv(args.parent_scores),
        json.loads(args.archive_json.read_text(encoding="utf-8-sig")),
    )
    fragments = select_pepglad_fragments(_read_csv(args.pepglad_fragments))
    history_hashes = asyncio.run(_historical_sequence_sha256s())
    prior_edits = load_prior_edits(args.history_csv)
    proposals = generate_proposals(
        parents,
        fragments,
        prior_edits=prior_edits,
        history_hashes=history_hashes,
        generation=args.generation,
        max_total=args.max_total,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if proposals:
        _write_csv(args.output_dir / "proposals.csv", proposals)
    receipt = {
        "schema_version": "ampgent.pbp2a-pepglad-source-graft.1",
        "operator_id": "pbp2a-pepglad-source-graft-v1",
        "target_key": "pbp2a",
        "parent_selection": (
            "PBP2a archive elite + display + formal12 + calibrated activity_support>=2"
        ),
        "parent_count": len(parents),
        "pepglad_fragment_count": len(fragments),
        "proposal_count": len(proposals),
        "proposal_limit": args.max_total,
        "prior_edit_count": len(prior_edits),
        "historical_sequence_hash_count": len(history_hashes),
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
        "gpu_rosetta_md_submitted": False,
    }
    (args.output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
