from __future__ import annotations

import csv
import hashlib
import io
import math
import statistics
from collections import defaultdict
from typing import Any

from pepagent.selection import sequence_distance

REQUIRED_METRICS = (
    "boltz2_pair_iptm",
    "boltz2_pair_iptm_median",
    "pocket_contact_count",
    "pocket_coverage_fraction",
    "off_pocket_contact_fraction",
    "interface_clash_count",
    "interface_min_distance_angstrom",
    "rosetta_dg_separated_reu",
    "rosetta_dg_minimum_reu",
    "rosetta_peptide_bb_rmsd_angstrom",
    "rosetta_interface_score",
    "rosetta_reweighted_score",
    "rosetta_interface_hbonds",
    "rosetta_buried_surface_area",
)

CANDIDATE_COLUMNS = (
    "screening_rank",
    "generator_id",
    "generator_seed",
    "within_seed_diversity_rank",
    "source_id",
    "source_candidate_id",
    "source_selected_rank",
    "sequence",
    "sequence_sha256",
    "candidate_id",
    "boltz2_tool_call_id",
    "coordinate_audit_tool_call_id",
    "rosetta_tool_call_id",
    *REQUIRED_METRICS,
)

SUMMARY_METRICS = (
    "boltz2_pair_iptm",
    "pocket_contact_count",
    "pocket_coverage_fraction",
    "off_pocket_contact_fraction",
    "interface_clash_count",
    "rosetta_dg_separated_reu",
    "rosetta_dg_minimum_reu",
    "rosetta_peptide_bb_rmsd_angstrom",
    "rosetta_interface_hbonds",
    "rosetta_buried_surface_area",
)

V31B_PARETO_DIRECTIONS = {
    "boltz2_pair_iptm": "maximize",
    "pocket_coverage_fraction": "maximize",
    "interface_clash_count": "minimize",
    "rosetta_dg_separated_reu": "minimize",
}

V31B_COHORT_COLUMNS = (
    "confirmation_rank",
    "generator_id",
    "within_generator_confirmation_rank",
    "pareto_front",
    "phase_a_screening_rank",
    "candidate_id",
    "source_candidate_id",
    "sequence",
    "sequence_sha256",
    *V31B_PARETO_DIRECTIONS,
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def render_csv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column) for column in columns})
    return stream.getvalue().encode("utf-8")


def build_candidate_rows(
    cohort_rows: list[dict[str, str]], evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    if len(cohort_rows) != 90:
        raise ValueError(f"expected 90 frozen cohort rows, found {len(cohort_rows)}")
    sequence_hashes = [row["sequence_sha256"] for row in cohort_rows]
    if len(set(sequence_hashes)) != len(sequence_hashes):
        raise ValueError("frozen cohort contains duplicate sequence_sha256 values")

    calls_by_id = {call["id"]: call for call in evidence["tool_calls"]}
    metrics: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    candidate_by_hash: dict[str, str] = {}
    for evaluation in evidence["evaluations"]:
        digest = evaluation["candidate_sequence_sha256"]
        candidate_id = evaluation["candidate_id"]
        previous = candidate_by_hash.setdefault(digest, candidate_id)
        if previous != candidate_id:
            raise ValueError(f"sequence hash maps to multiple candidates: {digest}")
        metrics[digest][evaluation["metric_name"]].append(evaluation)

    rows: list[dict[str, Any]] = []
    for cohort in cohort_rows:
        digest = cohort["sequence_sha256"]
        candidate_id = candidate_by_hash.get(digest)
        if candidate_id is None:
            raise ValueError(f"missing candidate mapping for {digest}")
        row: dict[str, Any] = dict(cohort)
        row["candidate_id"] = candidate_id
        tool_ids: dict[str, str] = {}
        for metric_name in REQUIRED_METRICS:
            matches = metrics[digest].get(metric_name, [])
            if len(matches) != 1:
                raise ValueError(
                    f"expected one {metric_name} evaluation for {digest}, found {len(matches)}"
                )
            evaluation = matches[0]
            value = evaluation["numeric_value"]
            if value is None or not math.isfinite(float(value)):
                raise ValueError(f"non-finite {metric_name} for {digest}")
            if evaluation["status"] != "succeeded":
                raise ValueError(f"non-succeeded {metric_name} for {digest}")
            row[metric_name] = value
            call = calls_by_id[evaluation["tool_call_id"]]
            if call["status"] != "succeeded":
                raise ValueError(f"non-succeeded tool call {call['id']}")
            tool_ids[call["tool_name"]] = call["id"]

        expected_tools = {
            "boltz2",
            "coordinate-interface-audit",
            "pyrosetta-flexpepdock-interface-analyzer",
        }
        if set(tool_ids) != expected_tools:
            raise ValueError(f"incomplete tool mapping for {digest}: {sorted(tool_ids)}")
        row["boltz2_tool_call_id"] = tool_ids["boltz2"]
        row["coordinate_audit_tool_call_id"] = tool_ids["coordinate-interface-audit"]
        row["rosetta_tool_call_id"] = tool_ids[
            "pyrosetta-flexpepdock-interface-analyzer"
        ]
        rows.append(row)

    evidence_hashes = set(candidate_by_hash)
    if evidence_hashes != set(sequence_hashes):
        extra = sorted(evidence_hashes - set(sequence_hashes))
        missing = sorted(set(sequence_hashes) - evidence_hashes)
        raise ValueError(f"evidence/cohort mismatch: extra={extra}, missing={missing}")
    return rows


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def summary_columns(group_fields: tuple[str, ...]) -> tuple[str, ...]:
    columns = [*group_fields, "candidate_count"]
    for metric in SUMMARY_METRICS:
        columns.extend(
            (f"{metric}_median", f"{metric}_q25", f"{metric}_q75", f"{metric}_min")
        )
    return tuple(columns)


def build_summary_rows(
    candidate_rows: list[dict[str, Any]], group_fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        grouped[tuple(str(row[field]) for field in group_fields)].append(row)
    output: list[dict[str, Any]] = []
    for key in sorted(grouped):
        members = grouped[key]
        summary = dict(zip(group_fields, key, strict=True))
        summary["candidate_count"] = len(members)
        for metric in SUMMARY_METRICS:
            values = [float(member[metric]) for member in members]
            summary[f"{metric}_median"] = statistics.median(values)
            summary[f"{metric}_q25"] = _quantile(values, 0.25)
            summary[f"{metric}_q75"] = _quantile(values, 0.75)
            summary[f"{metric}_min"] = min(values)
        output.append(summary)
    return output


def _dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    no_worse = True
    strictly_better = False
    for metric, direction in V31B_PARETO_DIRECTIONS.items():
        left_value = float(left[metric])
        right_value = float(right[metric])
        if direction == "maximize":
            no_worse = no_worse and left_value >= right_value
            strictly_better = strictly_better or left_value > right_value
        else:
            no_worse = no_worse and left_value <= right_value
            strictly_better = strictly_better or left_value < right_value
    return no_worse and strictly_better


def _pareto_fronts(rows: list[dict[str, Any]]) -> dict[str, int]:
    remaining = list(rows)
    fronts: dict[str, int] = {}
    front_index = 1
    while remaining:
        current = [
            row
            for row in remaining
            if not any(_dominates(other, row) for other in remaining if other is not row)
        ]
        if not current:
            raise RuntimeError("Pareto decomposition failed to make progress")
        for row in current:
            fronts[str(row["sequence_sha256"])] = front_index
            remaining.remove(row)
        front_index += 1
    return fronts


def select_v31b_confirmation_cohort(
    candidate_rows: list[dict[str, Any]], selected_per_generator: int = 6
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(candidate_rows) != 90:
        raise ValueError(f"expected 90 Phase A candidates, found {len(candidate_rows)}")
    by_generator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        by_generator[str(row["generator_id"])].append(row)
    if set(by_generator) != {"hydramp", "ampgan_v2", "amp_designer"}:
        raise ValueError("v31b requires exactly the three preregistered generators")
    if any(len(rows) != 30 for rows in by_generator.values()):
        raise ValueError("v31b requires 30 Phase A candidates per generator")

    selected: list[dict[str, Any]] = []
    audit_cells: list[dict[str, Any]] = []
    for generator_id in ("hydramp", "ampgan_v2", "amp_designer"):
        source = by_generator[generator_id]
        eligible = []
        for row in source:
            values = [float(row[metric]) for metric in V31B_PARETO_DIRECTIONS]
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"non-finite Pareto metric for {row['sequence_sha256']}")
            if float(row["pocket_contact_count"]) < 1:
                continue
            if float(row["interface_clash_count"]) >= 25:
                continue
            eligible.append(row)
        if len(eligible) < selected_per_generator:
            raise ValueError(f"insufficient eligible candidates for {generator_id}")
        fronts = _pareto_fronts(eligible)
        available = list(eligible)
        chosen: list[dict[str, Any]] = []
        while len(chosen) < selected_per_generator:
            earliest_front = min(fronts[str(row["sequence_sha256"])] for row in available)
            front_rows = [
                row
                for row in available
                if fronts[str(row["sequence_sha256"])] == earliest_front
            ]
            if not chosen:
                next_row = min(front_rows, key=lambda row: str(row["sequence_sha256"]))
            else:
                next_row = min(
                    front_rows,
                    key=lambda row: (
                        -min(
                            sequence_distance(str(row["sequence"]), str(item["sequence"]))
                            / max(len(str(row["sequence"])), len(str(item["sequence"])))
                            for item in chosen
                        ),
                        str(row["sequence_sha256"]),
                    ),
                )
            chosen.append(next_row)
            available.remove(next_row)

        front_counts: dict[str, int] = defaultdict(int)
        for rank, row in enumerate(chosen, start=1):
            front = fronts[str(row["sequence_sha256"])]
            front_counts[str(front)] += 1
            selected.append(
                {
                    "confirmation_rank": len(selected) + 1,
                    "generator_id": generator_id,
                    "within_generator_confirmation_rank": rank,
                    "pareto_front": front,
                    "phase_a_screening_rank": row["screening_rank"],
                    "candidate_id": row["candidate_id"],
                    "source_candidate_id": row["source_candidate_id"],
                    "sequence": row["sequence"],
                    "sequence_sha256": row["sequence_sha256"],
                    **{metric: row[metric] for metric in V31B_PARETO_DIRECTIONS},
                }
            )
        audit_cells.append(
            {
                "generator_id": generator_id,
                "source_count": len(source),
                "eligible_count": len(eligible),
                "selected_count": len(chosen),
                "selected_front_counts": dict(sorted(front_counts.items())),
            }
        )
    hashes = [str(row["sequence_sha256"]) for row in selected]
    if len(selected) != 18 or len(set(hashes)) != 18:
        raise ValueError("v31b confirmation cohort must contain 18 unique sequences")
    audit = {
        "method": "generator_stratified_pareto_then_maximin_sequence_distance",
        "selected_count": len(selected),
        "global_sequence_uniqueness": True,
        "metric_directions": V31B_PARETO_DIRECTIONS,
        "cells": audit_cells,
    }
    return selected, audit
