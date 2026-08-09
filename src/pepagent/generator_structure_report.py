from __future__ import annotations

import csv
import hashlib
import io
import math
import statistics
from collections import defaultdict
from typing import Any

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
