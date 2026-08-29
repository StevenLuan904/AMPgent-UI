from __future__ import annotations

import argparse
import csv
import io
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pepagent.autoresearch_score_ingest import (
    FORMAL_SCORE_COLUMNS,
    GURUPRASAD_OOD_COLUMN,
    PRIMARY_IDENTITY_COLUMNS,
    RAW_OCCURRENCE_REQUIRED_COLUMNS,
    validate_score_all_bundle,
    validate_score_source_map_receipt,
)
from pepagent.provenance.hashing import sha256_bytes, sha256_file, sha256_json, sha256_text

SOURCE_SCHEMA_VERSION = "ampgent.autoresearch-scoreall-bundle.v2"
OUTPUT_SCHEMA_VERSION = "ampgent.autoresearch-scoreall-bundle.v1"
ADAPTER_RECEIPT_SCHEMA_VERSION = "ampgent.autoresearch-score-v2-selection-adapter.1"
STRUCTURE_EXCLUSIONS_SCHEMA_VERSION = "ampgent.autoresearch-structure-exclusions.v1"
EXPECTED_FAMILY_SCOPE = "global_strict_library_80_identity_80_coverage"
SELECTION_METADATA_COLUMNS = ("v9_dry_rank", "v9_dry_lane")
CONSENSUS_LANE = "consensus_support_ge_2"
SUPPLEMENTAL_LANE = "supplemental_safe_instability_score_qualified"
LEGACY_SUPPLEMENTAL_LANE = "supplemental_safe_ood_qualified"
_CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
_HEX = frozenset("0123456789abcdef")
_LABEL_SCORE_COLUMNS = frozenset({"toxinpred3_label", "macrel_hemolysis_label"})


@dataclass(frozen=True)
class AdaptedScoreBundle:
    output_dir: Path
    bundle_receipt: dict[str, Any]
    bundle_receipt_sha256: str
    source_map_receipt: dict[str, Any]
    source_map_receipt_sha256: str
    adapter_receipt: dict[str, Any]
    target_counts: dict[str, int]
    formal_evaluation_count: int


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and not (set(value) - _HEX)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _csv_bytes(rows: Sequence[Mapping[str, str]], fieldnames: Sequence[str]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if not fields or len(set(fields)) != len(fields):
            raise ValueError(f"CSV header is empty or duplicated: {path}")
        return fields, [dict(row) for row in reader]


def _parse_bool(value: Any, *, field: str) -> bool:
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{field} is not a canonical boolean")


def _integer(value: Any, *, field: str) -> int:
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError(f"{field} is not an integer")
    return int(numeric)


def _finite(value: Any, *, field: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field} is not finite")
    return numeric


def _manifest_entries(payload: bytes, *, expected_count: int) -> dict[str, str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("v2 source manifest is not UTF-8") from error
    if len(lines) != expected_count:
        raise ValueError("v2 source manifest file count differs from its receipt")
    entries: dict[str, str] = {}
    for line in lines:
        digest, separator, path = line.partition("  ")
        normalized_path = path.replace("\\", "/")
        if (
            separator != "  "
            or not _is_sha256(digest)
            or not normalized_path
            or PurePosixPath(normalized_path).is_absolute()
            or ".." in PurePosixPath(normalized_path).parts
            or normalized_path in entries
        ):
            raise ValueError("v2 source manifest is not standard sha256sum format")
        entries[normalized_path] = digest
    return entries


def _validate_source_identity(
    *,
    source_bundle_receipt_path: Path,
    source_manifest_path: Path,
    source_strict_library_path: Path,
    expected_source_bundle_receipt_sha256: str,
    expected_source_strict_library_sha256: str,
) -> tuple[dict[str, Any], str]:
    if not _is_sha256(expected_source_bundle_receipt_sha256):
        raise ValueError("expected v2 bundle receipt SHA-256 is invalid")
    if not _is_sha256(expected_source_strict_library_sha256):
        raise ValueError("expected v2 strict-library SHA-256 is invalid")
    receipt_bytes = source_bundle_receipt_path.read_bytes()
    if sha256_bytes(receipt_bytes) != expected_source_bundle_receipt_sha256:
        raise ValueError("v2 bundle receipt differs from the requested source identity")
    try:
        receipt = json.loads(receipt_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("v2 bundle receipt is not valid UTF-8 JSON") from error
    if not isinstance(receipt, Mapping):
        raise ValueError("v2 bundle receipt must be a JSON object")
    if receipt.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise ValueError("source bundle is not the explicit score-all v2 schema")
    if receipt.get("status") != "succeeded" or not str(receipt.get("run_id") or ""):
        raise ValueError("v2 source bundle is not a succeeded identified run")
    source_uri = str(receipt.get("storage_uri") or "")
    if not source_uri.startswith("ssh://"):
        raise ValueError("v2 source bundle is not stored in canonical remote CAS")

    strict_ref = receipt.get("global_strict_library")
    manifest_ref = receipt.get("manifest")
    if not isinstance(strict_ref, Mapping) or not isinstance(manifest_ref, Mapping):
        raise ValueError("v2 source receipt lacks strict-library or manifest reference")
    strict_digest = str(strict_ref.get("sha256") or "")
    strict_relative_path = str(strict_ref.get("path") or "").replace("\\", "/")
    if (
        strict_digest != expected_source_strict_library_sha256
        or not strict_relative_path
        or not _is_sha256(strict_digest)
    ):
        raise ValueError("v2 strict-library receipt identity differs from the request")
    if sha256_file(source_strict_library_path) != strict_digest:
        raise ValueError("local v2 strict-library bytes differ from the frozen source")

    manifest_bytes = source_manifest_path.read_bytes()
    manifest_digest = str(manifest_ref.get("sha256") or "")
    if sha256_bytes(manifest_bytes) != manifest_digest:
        raise ValueError("local v2 manifest bytes differ from the frozen source")
    try:
        expected_file_count = int(manifest_ref["file_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("v2 source manifest file count is invalid") from error
    manifest_entries = _manifest_entries(manifest_bytes, expected_count=expected_file_count)
    if manifest_entries.get(strict_relative_path) != strict_digest:
        raise ValueError("v2 strict library is absent or differs from its source manifest")
    runtime = receipt.get("runtime")
    registry_sha256 = str(runtime.get("registry_sha256") or "") if isinstance(
        runtime, Mapping
    ) else ""
    if not _is_sha256(registry_sha256):
        raise ValueError("v2 source receipt lacks a valid metric-registry identity")
    return dict(receipt), registry_sha256


def _normalize_exclusions(
    structure_exclusions: Mapping[str, Any],
) -> tuple[set[str], set[str], str]:
    if structure_exclusions.get("schema_version") != STRUCTURE_EXCLUSIONS_SCHEMA_VERSION:
        raise ValueError("structure-exclusion input schema is not explicit")
    if structure_exclusions.get("status") != "complete":
        raise ValueError("structure-exclusion input is not complete")
    raw_sequences = structure_exclusions.get("sequence_sha256s")
    raw_families = structure_exclusions.get("family_keys")
    if not isinstance(raw_sequences, list) or not isinstance(raw_families, list):
        raise ValueError("structure-exclusion input must contain sequence and family lists")
    sequences = {str(value).strip().lower() for value in raw_sequences}
    families = {str(value).strip() for value in raw_families}
    if len(sequences) != len(raw_sequences) or len(families) != len(raw_families):
        raise ValueError("structure-exclusion input contains duplicate identities")
    if any(not _is_sha256(value) for value in sequences):
        raise ValueError("structure-exclusion input contains an invalid sequence SHA-256")
    if any(not value for value in families):
        raise ValueError("structure-exclusion input contains an empty family identity")
    return sequences, families, sha256_json(structure_exclusions)


def _validate_selected_row(
    row: Mapping[str, str],
    *,
    row_number: int,
    expected_targets: set[str],
    excluded_sequences: set[str],
    excluded_families: set[str],
) -> tuple[str, str, str, int, int]:
    target = str(row["target_key"]).strip().casefold()
    if target not in expected_targets:
        raise ValueError(f"selection row {row_number} has an unexpected target: {target}")
    sequence = str(row["sequence"])
    digest = str(row["sequence_sha256"]).strip().lower()
    family = str(row["family_key_80_80"]).strip()
    if sequence != sequence.strip().upper() or set(sequence) - _CANONICAL_AMINO_ACIDS:
        raise ValueError(f"selection row {row_number} has a non-canonical sequence")
    if not 10 <= len(sequence) <= 30:
        raise ValueError(
            f"selection row {row_number} is outside the existing v1 import length range"
        )
    if not _is_sha256(digest) or sha256_text(sequence) != digest:
        raise ValueError(f"selection row {row_number} sequence identity drifted")
    if not family or row["family_clustering_scope"] != EXPECTED_FAMILY_SCOPE:
        raise ValueError(f"selection row {row_number} lacks the published global family")
    if digest in excluded_sequences or family in excluded_families:
        raise ValueError(f"selection row {row_number} overlaps structure history")
    if not all(
        _parse_bool(row[field], field=field)
        for field in (
            "valid_sequence",
            "formal_metrics_complete",
            "display_eligible",
            "safety_labels_pass",
            "instability_lt_50",
        )
    ):
        raise ValueError(f"selection row {row_number} fails a strict literal gate")
    if _integer(row["formal_metric_count"], field="formal_metric_count") != 12:
        raise ValueError(f"selection row {row_number} does not declare 12 formal metrics")
    if _finite(
        row["guruprasad_instability_index"], field="guruprasad_instability_index"
    ) >= 50:
        raise ValueError(f"selection row {row_number} fails the instability gate")
    if row["toxinpred3_label"].strip().casefold() not in {
        "non-toxin",
        "non-toxic",
        "nontoxic",
    }:
        raise ValueError(f"selection row {row_number} fails the toxicity gate")
    if row["macrel_hemolysis_label"].strip().casefold() != "low":
        raise ValueError(f"selection row {row_number} fails the hemolysis gate")
    for metric in FORMAL_SCORE_COLUMNS:
        value = str(row[metric]).strip()
        if not value:
            raise ValueError(f"selection row {row_number} lacks formal metric {metric}")
        if metric not in _LABEL_SCORE_COLUMNS:
            _finite(value, field=metric)
    support = _integer(row["activity_model_support_count"], field="activity_model_support_count")
    if support not in {0, 1, 2, 3}:
        raise ValueError(f"selection row {row_number} has invalid activity support")
    lane = str(row["v9_dry_lane"]).strip()
    if (lane == CONSENSUS_LANE) != (support >= 2):
        raise ValueError(f"selection row {row_number} lane differs from activity support")
    if lane not in {CONSENSUS_LANE, SUPPLEMENTAL_LANE, LEGACY_SUPPLEMENTAL_LANE}:
        raise ValueError(f"selection row {row_number} has an unknown selection lane")
    rank = _integer(row["v9_dry_rank"], field="v9_dry_rank")
    if rank < 1:
        raise ValueError(f"selection row {row_number} has an invalid target rank")
    source_result = str(row["source_result"]).strip()
    source_result_sha256 = str(row["source_result_sha256"]).strip().lower()
    if not source_result or not _is_sha256(source_result_sha256):
        raise ValueError(f"selection row {row_number} lacks source-result identity")
    return target, digest, family, rank, support


def _join_selection_to_source(
    *,
    source_strict_library_path: Path,
    selection_csv_path: Path,
    expected_target_counts: Mapping[str, int],
    excluded_sequences: set[str],
    excluded_families: set[str],
) -> tuple[tuple[str, ...], tuple[str, ...], list[dict[str, str]], dict[str, int]]:
    selection_fields, selection_rows = _read_csv(selection_csv_path)
    required_fields = {
        *PRIMARY_IDENTITY_COLUMNS,
        *FORMAL_SCORE_COLUMNS,
        GURUPRASAD_OOD_COLUMN,
        *RAW_OCCURRENCE_REQUIRED_COLUMNS,
        "activity_model_support_count",
        "display_eligible",
        "family_clustering_scope",
        "family_key_80_80",
        "formal_metric_count",
        "formal_metrics_complete",
        "instability_lt_50",
        "safety_labels_pass",
        *SELECTION_METADATA_COLUMNS,
    }
    missing = required_fields - set(selection_fields)
    if missing:
        raise ValueError(f"selection CSV misses required columns: {sorted(missing)}")
    normalized_counts = {
        str(target).strip().casefold(): int(count)
        for target, count in expected_target_counts.items()
    }
    if not normalized_counts or any(count < 1 for count in normalized_counts.values()):
        raise ValueError("expected target counts must be positive")
    if len(normalized_counts) != len(expected_target_counts):
        raise ValueError("expected target counts contain normalized duplicate targets")

    selection_by_sequence: dict[str, dict[str, str]] = {}
    global_families: set[str] = set()
    target_counts: Counter[str] = Counter()
    target_ranks: dict[str, set[int]] = {target: set() for target in normalized_counts}
    for row_number, row in enumerate(selection_rows, start=2):
        target, digest, family, rank, _support = _validate_selected_row(
            row,
            row_number=row_number,
            expected_targets=set(normalized_counts),
            excluded_sequences=excluded_sequences,
            excluded_families=excluded_families,
        )
        if digest in selection_by_sequence:
            raise ValueError("selection is not globally sequence-unique")
        if family in global_families:
            raise ValueError("selection is not globally family-unique")
        if rank in target_ranks[target]:
            raise ValueError(f"selection target {target} contains a duplicate rank")
        selection_by_sequence[digest] = row
        global_families.add(family)
        target_counts[target] += 1
        target_ranks[target].add(rank)
    if dict(target_counts) != normalized_counts:
        raise ValueError(
            "selection target counts differ: "
            f"expected {normalized_counts}, got {dict(target_counts)}"
        )
    for target, count in normalized_counts.items():
        if target_ranks[target] != set(range(1, count + 1)):
            raise ValueError(f"selection target {target} ranks are not contiguous")

    source_matches: dict[str, dict[str, str]] = {}
    with source_strict_library_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        source_fields = tuple(reader.fieldnames or ())
        if not source_fields or len(set(source_fields)) != len(source_fields):
            raise ValueError("v2 strict-library CSV header is empty or duplicated")
        missing_source = set(source_fields) - set(selection_fields)
        if missing_source:
            raise ValueError(
                f"selection does not preserve v2 strict source columns: {sorted(missing_source)}"
            )
        selection_only = set(selection_fields) - set(source_fields)
        if selection_only != set(SELECTION_METADATA_COLUMNS):
            raise ValueError(
                "selection-only columns differ from the explicit adapter contract: "
                f"{sorted(selection_only)}"
            )
        for row in reader:
            digest = str(row.get("sequence_sha256") or "").strip().lower()
            selected = selection_by_sequence.get(digest)
            if selected is None:
                continue
            if digest in source_matches:
                raise ValueError("selected sequence occurs more than once in v2 strict source")
            drifted = [name for name in source_fields if str(selected[name]) != str(row[name])]
            if drifted:
                raise ValueError(
                    "selection row differs from v2 strict source columns: "
                    f"{digest} {drifted}"
                )
            source_matches[digest] = dict(row)
    missing_sequences = set(selection_by_sequence) - set(source_matches)
    if missing_sequences:
        raise ValueError(
            f"selection contains rows absent from v2 strict source: {len(missing_sequences)}"
        )

    target_order = {target: index for index, target in enumerate(normalized_counts)}
    ordered = sorted(
        selection_rows,
        key=lambda row: (
            target_order[str(row["target_key"]).strip().casefold()],
            int(float(row["v9_dry_rank"])),
            row["sequence_sha256"],
        ),
    )
    output_rows = []
    for row in ordered:
        source = source_matches[row["sequence_sha256"]]
        output_rows.append(
            {
                **source,
                **{name: row[name] for name in selection_fields if name not in source},
            }
        )
    return selection_fields, source_fields, output_rows, normalized_counts


def adapt_v2_selection_to_v1_bundle(
    *,
    source_bundle_receipt_path: Path,
    source_manifest_path: Path,
    source_strict_library_path: Path,
    selection_csv_path: Path,
    structure_exclusions: Mapping[str, Any],
    expected_target_counts: Mapping[str, int],
    expected_source_bundle_receipt_sha256: str,
    expected_source_strict_library_sha256: str,
    output_dir: Path,
    run_id: str,
    created_at: str,
    storage_uri_prefix: str,
    adapter_revision: str,
) -> AdaptedScoreBundle:
    """Adapt selected v2 rows to a new, explicit v1 import bundle without rescoring."""

    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("adapter output directory must be absent or empty")
    if not str(run_id).strip() or not str(created_at).strip() or not str(adapter_revision).strip():
        raise ValueError("adapter run_id, created_at, and revision are required")
    normalized_prefix = str(storage_uri_prefix).rstrip("/") + "/"
    if not normalized_prefix.startswith("ssh://"):
        raise ValueError("adapter storage URI prefix must be canonical remote SSH CAS")

    source_receipt, registry_sha256 = _validate_source_identity(
        source_bundle_receipt_path=source_bundle_receipt_path,
        source_manifest_path=source_manifest_path,
        source_strict_library_path=source_strict_library_path,
        expected_source_bundle_receipt_sha256=expected_source_bundle_receipt_sha256,
        expected_source_strict_library_sha256=expected_source_strict_library_sha256,
    )
    excluded_sequences, excluded_families, exclusions_sha256 = _normalize_exclusions(
        structure_exclusions
    )
    output_fields, source_fields, rows, target_counts = _join_selection_to_source(
        source_strict_library_path=source_strict_library_path,
        selection_csv_path=selection_csv_path,
        expected_target_counts=expected_target_counts,
        excluded_sequences=excluded_sequences,
        excluded_families=excluded_families,
    )
    row_count = len(rows)
    formal_evaluation_count = row_count * len(FORMAL_SCORE_COLUMNS)
    target_stats: dict[str, dict[str, int]] = {}
    for target, count in target_counts.items():
        target_rows = [row for row in rows if row["target_key"].strip().casefold() == target]
        target_stats[target] = {
            "rows": count,
            "formal_evaluations": count * len(FORMAL_SCORE_COLUMNS),
            "consensus": sum(
                row["v9_dry_lane"] == CONSENSUS_LANE for row in target_rows
            ),
            "supplemental": sum(
                row["v9_dry_lane"]
                in {SUPPLEMENTAL_LANE, LEGACY_SUPPLEMENTAL_LANE}
                for row in target_rows
            ),
        }

    primary_path = "score/all_scored_audit.csv"
    strict_path = "score/usable_instability_lt50.csv"
    raw_path = "score/raw_occurrence_audit.csv"
    score_receipt_path = "score/score.receipt.json"
    adapter_receipt_path = "score/schema_adapter.receipt.json"
    primary_bytes = _csv_bytes(rows, output_fields)
    strict_bytes = primary_bytes
    raw_bytes = primary_bytes
    primary_sha256 = sha256_bytes(primary_bytes)
    storage_uri = f"{normalized_prefix}{primary_sha256}/"

    selection_sha256 = sha256_file(selection_csv_path)
    adapter_receipt = {
        "schema_version": ADAPTER_RECEIPT_SCHEMA_VERSION,
        "status": "succeeded",
        "created_at": created_at,
        "adapter_run_id": run_id,
        "adapter_revision": adapter_revision,
        "source": {
            "schema_version": SOURCE_SCHEMA_VERSION,
            "run_id": source_receipt["run_id"],
            "bundle_receipt_sha256": expected_source_bundle_receipt_sha256,
            "strict_library_sha256": expected_source_strict_library_sha256,
            "storage_uri": source_receipt["storage_uri"],
        },
        "selection": {
            "sha256": selection_sha256,
            "metadata_columns": list(SELECTION_METADATA_COLUMNS),
            "target_counts": target_counts,
            "target_stats": target_stats,
            "global_sequence_count": row_count,
            "global_family_count": row_count,
        },
        "structure_exclusions": {
            "input_sha256": exclusions_sha256,
            "sequence_count": len(excluded_sequences),
            "family_count": len(excluded_families),
            "selected_sequence_overlap": 0,
            "selected_family_overlap": 0,
        },
        "preservation": {
            "source_columns": list(source_fields),
            "selection_metadata_columns": list(SELECTION_METADATA_COLUMNS),
            "output_columns": list(output_fields),
            "target_key_preserved": True,
            "source_result_preserved": True,
            "family_identity_preserved": True,
            "formal_12_values_preserved_byte_for_byte": True,
            "scientific_metrics_recomputed": False,
        },
        "output": {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "rows": row_count,
            "formal_evaluations": formal_evaluation_count,
            "storage_uri": storage_uri,
        },
        "database_mutated": False,
        "workflow_submitted": False,
    }
    adapter_receipt_bytes = _json_bytes(adapter_receipt)
    score_receipt = {
        "schema_version": "ampgent.autoresearch-score-v2-selection-adapter-score.v1",
        "status": "succeeded",
        "created_at": created_at,
        "source_bundle_schema": SOURCE_SCHEMA_VERSION,
        "output_import_schema": OUTPUT_SCHEMA_VERSION,
        "schema_adapter_only": True,
        "scientific_metrics_recomputed": False,
        "formal_metric_names": list(FORMAL_SCORE_COLUMNS),
        "row_count": row_count,
        "formal_evaluation_count": formal_evaluation_count,
        "target_counts": target_counts,
        "adapter_receipt": {
            "path": adapter_receipt_path,
            "sha256": sha256_bytes(adapter_receipt_bytes),
        },
    }
    score_receipt_bytes = _json_bytes(score_receipt)
    payloads = {
        primary_path: primary_bytes,
        strict_path: strict_bytes,
        raw_path: raw_bytes,
        score_receipt_path: score_receipt_bytes,
        adapter_receipt_path: adapter_receipt_bytes,
    }
    manifest_bytes = "".join(
        f"{sha256_bytes(payload)}  {path}\n" for path, payload in sorted(payloads.items())
    ).encode("utf-8")
    manifest_path = "MANIFEST.sha256"
    payloads[manifest_path] = manifest_bytes

    source_pairs = sorted(
        {
            (
                PurePosixPath(row["source_result"].replace("\\", "/")).name,
                row["source_result_sha256"],
            )
            for row in rows
        }
    )
    pair_to_label = {
        pair: f"v2_source_{index:04d}" for index, pair in enumerate(source_pairs, start=1)
    }
    split_counts: dict[tuple[str, str], Counter[str]] = {}
    for row in rows:
        pair = (
            PurePosixPath(row["source_result"].replace("\\", "/")).name,
            row["source_result_sha256"],
        )
        target = row["target_key"].strip().casefold()
        counter = split_counts.setdefault((pair_to_label[pair], target), Counter())
        counter["raw"] += 1
        counter["strict"] += 1
        support = int(float(row["activity_model_support_count"]))
        counter["activity_support_ge_2"] += int(support >= 2)
        counter["activity_support_3"] += int(support == 3)
        counter["new_unique"] += 1
    source_splits = [
        {
            "source": label,
            "target_key": target,
            "raw": counts["raw"],
            "strict": counts["strict"],
            "activity_support_ge_2": counts["activity_support_ge_2"],
            "activity_support_3": counts["activity_support_3"],
            "new_unique": counts["new_unique"],
        }
        for (label, target), counts in sorted(split_counts.items())
    ]
    support_ge_2 = sum(int(float(row["activity_model_support_count"])) >= 2 for row in rows)
    support_3 = sum(int(float(row["activity_model_support_count"])) == 3 for row in rows)
    bundle_receipt = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "status": "succeeded",
        "run_id": run_id,
        "created_at": created_at,
        "storage_uri": storage_uri,
        "content_address_key": primary_sha256,
        "primary_result": {"path": primary_path, "sha256": primary_sha256},
        "strict_subset": {"path": strict_path, "sha256": sha256_bytes(strict_bytes)},
        "raw_occurrence_audit": {"path": raw_path, "sha256": sha256_bytes(raw_bytes)},
        "score_receipt": {
            "path": score_receipt_path,
            "sha256": sha256_bytes(score_receipt_bytes),
        },
        "manifest": {
            "path": manifest_path,
            "sha256": sha256_bytes(manifest_bytes),
            "file_count": len(payloads) - 1,
        },
        "counts": {
            "raw_occurrences": row_count,
            "valid_unique": row_count,
            "formal_12_metrics_complete": row_count,
            "safety_labels_pass": row_count,
            "instability_lt_50": row_count,
            "strict_display_eligible": row_count,
            "activity_support_ge_2": support_ge_2,
            "activity_support_3": support_3,
        },
        "source_splits": source_splits,
        "family_analysis": {
            "scope": EXPECTED_FAMILY_SCOPE,
            "global_sequence_count": row_count,
            "global_family_count": row_count,
            "cross_target_sequence_overlap": 0,
            "cross_target_family_overlap": 0,
            "structure_sequence_overlap": 0,
            "structure_family_overlap": 0,
            "target_counts": target_counts,
        },
        "runtime": {
            "adapter_commit": adapter_revision,
            "registry_sha256": registry_sha256,
        },
        "warnings": [
            "schema adaptation only; all formal metric values were copied from score-all v2"
        ],
    }
    bundle_receipt_bytes = _json_bytes(bundle_receipt)
    bundle_receipt_sha256 = sha256_bytes(bundle_receipt_bytes)
    source_map_receipt = {
        "schema_version": "ampgent.score-source-map.v1",
        "status": "complete",
        "created_at": created_at,
        "runs": [
            {
                "run_id": run_id,
                "bundle_receipt_sha256": bundle_receipt_sha256,
                "mappings": [
                    {
                        "source_label": pair_to_label[pair],
                        "source_result_basename": pair[0],
                        "source_result_sha256": pair[1],
                    }
                    for pair in source_pairs
                ],
            }
        ],
    }
    source_map_receipt_bytes = _json_bytes(source_map_receipt)
    source_map_receipt_sha256 = sha256_bytes(source_map_receipt_bytes)
    validated_map = validate_score_source_map_receipt(
        receipt=source_map_receipt,
        receipt_sha256=source_map_receipt_sha256,
        receipt_bytes=source_map_receipt_bytes,
        source_run_id=run_id,
        bundle_receipt_sha256=bundle_receipt_sha256,
    )
    for target, expected_count in target_counts.items():
        validated = validate_score_all_bundle(
            bundle_receipt=bundle_receipt,
            bundle_receipt_sha256=bundle_receipt_sha256,
            bundle_receipt_bytes=bundle_receipt_bytes,
            bundle_receipt_relative_path="bundle.receipt.json",
            target_key=target,
            source_result_mappings=validated_map.source_result_mappings,
            read_bytes=lambda relative_path: payloads[relative_path],
        )
        if len(validated.primary_rows) != expected_count:
            raise AssertionError(f"adapted target {target} row count failed importer validation")

    final_payloads = {
        **payloads,
        "bundle.receipt.json": bundle_receipt_bytes,
        "score_source_map.receipt.json": source_map_receipt_bytes,
    }
    for relative_path, payload in final_payloads.items():
        destination = output_dir.joinpath(*relative_path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return AdaptedScoreBundle(
        output_dir=output_dir,
        bundle_receipt=bundle_receipt,
        bundle_receipt_sha256=bundle_receipt_sha256,
        source_map_receipt=source_map_receipt,
        source_map_receipt_sha256=source_map_receipt_sha256,
        adapter_receipt=adapter_receipt,
        target_counts=target_counts,
        formal_evaluation_count=formal_evaluation_count,
    )


def _load_json_mapping(path: Path, *, name: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return dict(value)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicitly adapt selected score-all v2 rows to the frozen v1 importer."
    )
    parser.add_argument("--source-bundle-receipt", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-strict-library", type=Path, required=True)
    parser.add_argument("--selection-csv", type=Path, required=True)
    parser.add_argument("--structure-exclusions", type=Path, required=True)
    parser.add_argument("--expected-target-counts", type=Path, required=True)
    parser.add_argument("--expected-source-bundle-receipt-sha256", required=True)
    parser.add_argument("--expected-source-strict-library-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--storage-uri-prefix", required=True)
    parser.add_argument("--adapter-revision", required=True)
    args = parser.parse_args()
    result = adapt_v2_selection_to_v1_bundle(
        source_bundle_receipt_path=args.source_bundle_receipt,
        source_manifest_path=args.source_manifest,
        source_strict_library_path=args.source_strict_library,
        selection_csv_path=args.selection_csv,
        structure_exclusions=_load_json_mapping(
            args.structure_exclusions, name="structure exclusions"
        ),
        expected_target_counts=_load_json_mapping(
            args.expected_target_counts, name="expected target counts"
        ),
        expected_source_bundle_receipt_sha256=args.expected_source_bundle_receipt_sha256,
        expected_source_strict_library_sha256=args.expected_source_strict_library_sha256,
        output_dir=args.output_dir,
        run_id=args.run_id,
        created_at=args.created_at,
        storage_uri_prefix=args.storage_uri_prefix,
        adapter_revision=args.adapter_revision,
    )
    print(
        json.dumps(
            {
                "schema_version": ADAPTER_RECEIPT_SCHEMA_VERSION,
                "status": "succeeded",
                "inert": True,
                "output_dir": str(result.output_dir),
                "bundle_receipt_sha256": result.bundle_receipt_sha256,
                "source_map_receipt_sha256": result.source_map_receipt_sha256,
                "target_counts": result.target_counts,
                "formal_evaluation_count": result.formal_evaluation_count,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()


__all__ = [
    "ADAPTER_RECEIPT_SCHEMA_VERSION",
    "AdaptedScoreBundle",
    "STRUCTURE_EXCLUSIONS_SCHEMA_VERSION",
    "adapt_v2_selection_to_v1_bundle",
]
