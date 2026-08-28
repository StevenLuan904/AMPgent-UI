from __future__ import annotations

import csv
import io
import math
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from pepagent.provenance.hashing import sha256_bytes, sha256_text

FORMAL_SCORE_COLUMNS = (
    "amp_read_log10_mic_um",
    "llamp_log10_mic_um",
    "macrel_amp_probability",
    "toxinpred3_label",
    "toxinpred3_hybrid_score",
    "macrel_hemolysis_label",
    "macrel_hemolysis_probability",
    "net_charge_ph7_4",
    "hydrophobic_ratio_modlamp",
    "hydrophobic_moment_eisenberg",
    "maximum_hydrophobic_run",
    "guruprasad_instability_index",
)
GURUPRASAD_OOD_COLUMN = "guruprasad_instability_ood"
PRIMARY_IDENTITY_COLUMNS = (
    "candidate_id",
    "sequence",
    "sequence_sha256",
    "target_key",
    "generator_id",
    "generator_seed",
    "raw_rank",
    "source_result",
    "source_result_sha256",
    "action_id",
    "action_kind",
    "action_seed",
    "action_sha256",
    "primary_parent_id",
    "donor_candidate_id",
    "lineage",
)
RAW_OCCURRENCE_COLUMNS = (
    "action_id",
    "action_kind",
    "action_seed",
    "action_sha256",
    "candidate_id",
    "conditional_nll",
    "conditional_ppl",
    "donor_candidate_id",
    "duplicate_within_expansion",
    "expected_improvement_axes",
    "generator_id",
    "generator_seed",
    "invalid_reason",
    "lineage",
    "mutation_positions",
    "parent_sequence",
    "per_residue_log_probabilities",
    "primary_parent_id",
    "proposal_mode",
    "protected_axes",
    "raw_rank",
    "sampling_attempt",
    "sampling_seed",
    "seed",
    "sequence",
    "sequence_sha256",
    "source_action_plan",
    "source_action_plan_sha256",
    "source_candidate",
    "source_candidate_sha256",
    "source_result",
    "source_result_sha256",
    "structure_file_name",
    "structure_relaxation_error",
    "structure_relaxation_status",
    "structure_sha256",
    "structure_size_bytes",
    "target_key",
    "valid_sequence",
)
_MANIFEST_LINE = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")
_CANONICAL = frozenset("ACDEFGHIKLMNPQRSTVWY")


@dataclass(frozen=True)
class ValidatedScoreAllBundle:
    receipt_sha256: str
    manifest_sha256: str
    storage_uri: str
    source_run_id: str
    target_key: str
    all_manifest_files: tuple[tuple[str, str], ...]
    primary_rows: tuple[dict[str, str], ...]
    raw_rows: tuple[dict[str, str], ...]
    strict_sequence_sha256s: tuple[str, ...]
    runtime: dict[str, Any]
    counts: dict[str, int]


@dataclass(frozen=True)
class ValidatedScoreSourceMap:
    receipt_sha256: str
    source_run_id: str
    bundle_receipt_sha256: str
    source_result_mappings: dict[str, tuple[str, str]]


def _safe_relative_path(value: str) -> str:
    normalized = str(value).strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe score bundle path: {value!r}")
    return path.as_posix()


def _file_ref(value: Any, name: str) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"score bundle {name} must be a file reference")
    path = _safe_relative_path(str(value.get("path") or ""))
    digest = str(value.get("sha256") or "")
    if len(digest) != 64 or set(digest) - set("0123456789abcdef"):
        raise ValueError(f"score bundle {name} SHA-256 is invalid")
    return path, digest


def _parse_manifest(payload: bytes, expected_count: int) -> dict[str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("score bundle manifest is not UTF-8") from error
    lines = text.splitlines()
    if len(lines) != expected_count:
        raise ValueError("score bundle manifest file count differs from its receipt")
    entries: dict[str, str] = {}
    for line in lines:
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise ValueError("score bundle manifest is not standard sha256sum format")
        digest, raw_path = match.groups()
        path = _safe_relative_path(raw_path)
        if path in entries:
            raise ValueError("score bundle manifest contains a duplicate path")
        entries[path] = digest
    return entries


def _read_bom_csv(
    payload: bytes,
    required_columns: tuple[str, ...],
    name: str,
) -> list[dict[str, str]]:
    if not payload.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"score bundle {name} must be UTF-8 BOM CSV")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"score bundle {name} is not valid UTF-8") from error
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = tuple(reader.fieldnames or ())
    missing = set(required_columns) - set(header)
    if missing:
        raise ValueError(f"score bundle {name} misses columns: {sorted(missing)}")
    return [dict(row) for row in reader]


def _parse_boolean(value: str, name: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"score bundle {name} is not boolean")


def _source_split_for_row(
    row: Mapping[str, str],
    splits: list[dict[str, Any]],
    source_result_mappings: Mapping[str, tuple[str, str]],
) -> int:
    source_result_basename = PurePosixPath(
        str(row["source_result"]).replace("\\", "/")
    ).name
    source_result_sha256 = str(row["source_result_sha256"])
    target_key = str(row["target_key"])
    matches = []
    for index, split in enumerate(splits):
        source = str(split["source"])
        if str(split["target_key"]) != target_key:
            continue
        if source_result_mappings[source] == (
            source_result_basename,
            source_result_sha256,
        ):
            matches.append(index)
    if len(matches) != 1:
        raise ValueError("raw occurrence does not map to exactly one declared source split")
    return matches[0]


def validate_score_source_map_receipt(
    *,
    receipt: Mapping[str, Any],
    receipt_sha256: str,
    receipt_bytes: bytes,
    source_run_id: str,
    bundle_receipt_sha256: str,
) -> ValidatedScoreSourceMap:
    """Validate the append-only exact basename map for one frozen score bundle."""

    if sha256_bytes(receipt_bytes) != receipt_sha256:
        raise OSError("score source-map receipt SHA-256 mismatch")
    if set(receipt) != {"schema_version", "status", "created_at", "runs"}:
        raise ValueError("score source-map receipt schema differs from its contract")
    if receipt.get("schema_version") != "ampgent.score-source-map.v1":
        raise ValueError("score source-map schema version is not frozen")
    if receipt.get("status") != "complete" or not str(receipt.get("created_at") or ""):
        raise ValueError("score source-map receipt is not complete")
    runs = receipt.get("runs")
    if not isinstance(runs, list):
        raise ValueError("score source-map runs must be a list")
    matches = [
        item
        for item in runs
        if isinstance(item, Mapping)
        and str(item.get("run_id")) == source_run_id
        and str(item.get("bundle_receipt_sha256")) == bundle_receipt_sha256
    ]
    if len(matches) != 1:
        raise ValueError("score bundle does not map to exactly one source-map run")
    run = matches[0]
    if set(run) != {"run_id", "bundle_receipt_sha256", "mappings"}:
        raise ValueError("score source-map run schema differs from its contract")
    mappings = run.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise ValueError("score source-map run has no mappings")
    result: dict[str, tuple[str, str]] = {}
    observed_pairs: set[tuple[str, str]] = set()
    for item in mappings:
        if not isinstance(item, Mapping) or set(item) != {
            "source_label",
            "source_result_basename",
            "source_result_sha256",
        }:
            raise ValueError("score source-map item schema differs from its contract")
        label = str(item["source_label"])
        basename = str(item["source_result_basename"])
        digest = str(item["source_result_sha256"])
        if (
            not label
            or not basename
            or PurePosixPath(basename.replace("\\", "/")).name != basename
            or len(digest) != 64
            or set(digest) - set("0123456789abcdef")
        ):
            raise ValueError("score source-map item identity is invalid")
        if label in result or (basename, digest) in observed_pairs:
            raise ValueError("score source-map mapping is not one-to-one")
        result[label] = (basename, digest)
        observed_pairs.add((basename, digest))
    return ValidatedScoreSourceMap(
        receipt_sha256=receipt_sha256,
        source_run_id=source_run_id,
        bundle_receipt_sha256=bundle_receipt_sha256,
        source_result_mappings=result,
    )


def validate_score_all_bundle(
    *,
    bundle_receipt: Mapping[str, Any],
    bundle_receipt_sha256: str,
    bundle_receipt_bytes: bytes,
    bundle_receipt_relative_path: str,
    target_key: str,
    source_result_mappings: Mapping[str, tuple[str, str]],
    read_bytes: Callable[[str], bytes],
) -> ValidatedScoreAllBundle:
    """Verify all CAS bytes, joins, source coverage, and the formal 12-score cohort."""

    required_receipt_fields = {
        "schema_version",
        "status",
        "run_id",
        "created_at",
        "storage_uri",
        "content_address_key",
        "primary_result",
        "strict_subset",
        "raw_occurrence_audit",
        "score_receipt",
        "manifest",
        "counts",
        "source_splits",
        "family_analysis",
        "runtime",
        "warnings",
    }
    if set(bundle_receipt) != required_receipt_fields:
        raise ValueError("score bundle receipt schema differs from the frozen contract")
    if not str(bundle_receipt.get("schema_version") or "").strip():
        raise ValueError("score bundle schema version is empty")
    if not str(bundle_receipt.get("run_id") or "").strip():
        raise ValueError("score bundle source run identity is empty")
    if not str(bundle_receipt.get("created_at") or "") or not isinstance(
        bundle_receipt.get("warnings"), list
    ):
        raise ValueError("score bundle creation time or warnings are invalid")
    if not isinstance(bundle_receipt.get("family_analysis"), Mapping):
        raise ValueError("score bundle family analysis is not an object")
    runtime = bundle_receipt.get("runtime")
    required_runtime_fields = {
        "adapter_commit",
        "adapter_sha",
        "scorer_sha",
        "registry_sha",
        "python_sha",
    }
    if not isinstance(runtime, Mapping) or set(runtime) != required_runtime_fields:
        raise ValueError("score bundle runtime schema differs from the frozen contract")
    counts_payload = bundle_receipt.get("counts")
    required_count_fields = {"raw", "formal12", "strict", "ge2", "three"}
    if not isinstance(counts_payload, Mapping) or set(counts_payload) != required_count_fields:
        raise ValueError("score bundle count schema differs from the frozen contract")
    if sha256_bytes(bundle_receipt_bytes) != bundle_receipt_sha256:
        raise OSError("score bundle receipt SHA-256 mismatch")
    if bundle_receipt.get("status") != "succeeded":
        raise ValueError("score bundle receipt is not succeeded")
    storage_uri = str(bundle_receipt.get("storage_uri") or "").rstrip("/") + "/"
    if not storage_uri.startswith("ssh://"):
        raise ValueError("score bundle storage URI must be the canonical remote SSH CAS")
    manifest_path, manifest_sha = _file_ref(bundle_receipt.get("manifest"), "manifest")
    expected_file_count = int(bundle_receipt["manifest"]["file_count"])
    if expected_file_count < 1:
        raise ValueError("score bundle manifest file count must be positive")
    manifest_bytes = read_bytes(manifest_path)
    if sha256_bytes(manifest_bytes) != manifest_sha:
        raise OSError("score bundle manifest SHA-256 mismatch")
    entries = _parse_manifest(manifest_bytes, expected_file_count)
    receipt_path = _safe_relative_path(bundle_receipt_relative_path)
    if receipt_path in entries:
        raise ValueError("score bundle manifest must not contain its bundle receipt")
    payloads: dict[str, bytes] = {}
    for path, expected_sha in sorted(entries.items()):
        payload = read_bytes(path)
        if sha256_bytes(payload) != expected_sha:
            raise OSError(f"score bundle file SHA-256 mismatch: {path}")
        payloads[path] = payload

    refs = {
        name: _file_ref(bundle_receipt.get(name), name)
        for name in (
            "primary_result",
            "strict_subset",
            "raw_occurrence_audit",
            "score_receipt",
        )
    }
    if str(bundle_receipt.get("content_address_key")) != refs["primary_result"][1]:
        raise ValueError("score bundle content-address key differs from its primary result")
    for name, (path, digest) in refs.items():
        if entries.get(path) != digest:
            raise ValueError(f"score bundle {name} is absent or differs from the manifest")

    primary = _read_bom_csv(
        payloads[refs["primary_result"][0]],
        (*PRIMARY_IDENTITY_COLUMNS, *FORMAL_SCORE_COLUMNS, GURUPRASAD_OOD_COLUMN),
        "primary result",
    )
    raw = _read_bom_csv(
        payloads[refs["raw_occurrence_audit"][0]],
        RAW_OCCURRENCE_COLUMNS,
        "raw occurrence audit",
    )
    strict = _read_bom_csv(
        payloads[refs["strict_subset"][0]],
        (*PRIMARY_IDENTITY_COLUMNS, *FORMAL_SCORE_COLUMNS, GURUPRASAD_OOD_COLUMN),
        "strict subset",
    )
    counts = {key: int(value) for key, value in dict(counts_payload).items()}
    if any(value < 0 for value in counts.values()):
        raise ValueError("score bundle counts must be non-negative")
    if not (
        counts["raw"] == counts["formal12"]
        and counts["three"] <= counts["ge2"] <= counts["strict"] <= counts["formal12"]
    ):
        raise ValueError("score bundle count hierarchy differs from score-all semantics")
    if len(raw) != counts["raw"] or len(primary) != counts["formal12"]:
        raise ValueError("score bundle raw/formal12 row counts differ from its receipt")
    if len(strict) != counts["strict"]:
        raise ValueError("score bundle strict row count differs from its receipt")

    primary_keys: set[tuple[str, str, str, str]] = set()
    primary_by_identity: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in primary:
        sequence = str(row["sequence"]).strip().upper()
        if not 10 <= len(sequence) <= 25 or set(sequence) - _CANONICAL:
            raise ValueError("primary score row contains an invalid short peptide")
        if sha256_text(sequence) != row["sequence_sha256"]:
            raise OSError("primary score row sequence SHA-256 mismatch")
        for metric in FORMAL_SCORE_COLUMNS:
            value = str(row[metric]).strip()
            if not value:
                raise ValueError(f"primary score row lacks formal metric: {metric}")
            if metric not in {"toxinpred3_label", "macrel_hemolysis_label"}:
                try:
                    numeric_value = float(value)
                except ValueError as error:
                    raise ValueError(f"formal metric is not numeric: {metric}") from error
                if not math.isfinite(numeric_value):
                    raise ValueError(f"formal metric is not finite: {metric}")
        _parse_boolean(row[GURUPRASAD_OOD_COLUMN], GURUPRASAD_OOD_COLUMN)
        identity = (
            str(row["candidate_id"]),
            str(row["source_result_sha256"]),
            str(row["raw_rank"]),
            str(row["sequence_sha256"]),
        )
        if identity in primary_keys:
            raise ValueError("primary score cohort contains duplicate raw identities")
        primary_keys.add(identity)
        primary_by_identity[identity] = row

    raw_keys: set[tuple[str, str, str, str]] = set()
    splits = [dict(item) for item in bundle_receipt.get("source_splits") or []]
    required_split_fields = {
        "source",
        "target_key",
        "raw",
        "strict",
        "activity_support_ge_2",
        "activity_support_3",
        "new_unique",
    }
    if not splits or any(set(item) != required_split_fields for item in splits):
        raise ValueError("score bundle source split schema differs from the frozen contract")
    split_sources = {str(item["source"]) for item in splits}
    if split_sources != set(source_result_mappings):
        raise ValueError("score source-map labels differ from bundle source splits")
    observed_split_counts: Counter[int] = Counter()
    for row in raw:
        sequence = str(row["sequence"]).strip().upper()
        if sha256_text(sequence) != row["sequence_sha256"]:
            raise OSError("raw occurrence sequence SHA-256 mismatch")
        if not _parse_boolean(row["valid_sequence"], "valid_sequence"):
            raise ValueError("formal12 score bundle contains an invalid raw occurrence")
        identity = (
            str(row["candidate_id"]),
            str(row["source_result_sha256"]),
            str(row["raw_rank"]),
            str(row["sequence_sha256"]),
        )
        if identity in raw_keys:
            raise ValueError("raw occurrence audit contains duplicate identities")
        raw_keys.add(identity)
        primary_row = primary_by_identity.get(identity)
        if primary_row is None or any(
            str(primary_row[name]) != str(row[name])
            for name in PRIMARY_IDENTITY_COLUMNS
        ):
            raise ValueError("raw occurrence identity differs from primary formal12")
        observed_split_counts[
            _source_split_for_row(row, splits, source_result_mappings)
        ] += 1
    if primary_keys != raw_keys:
        raise ValueError("primary formal12 cohort does not cover the complete raw audit")
    for index, split in enumerate(splits):
        if observed_split_counts[index] != int(split["raw"]):
            raise ValueError("source split raw count differs from occurrence coverage")
    if sum(int(item["raw"]) for item in splits) != counts["raw"]:
        raise ValueError("source split raw counts do not cover the bundle")
    for split_key, count_key in (
        ("strict", "strict"),
        ("activity_support_ge_2", "ge2"),
        ("activity_support_3", "three"),
    ):
        if sum(int(item[split_key]) for item in splits) != counts[count_key]:
            raise ValueError(f"source split {split_key} counts do not cover the bundle")

    strict_sha_by_target: list[tuple[str, str]] = []
    for row in strict:
        sequence = str(row["sequence"]).strip().upper()
        digest = str(row["sequence_sha256"])
        identity = (
            str(row["candidate_id"]),
            str(row["source_result_sha256"]),
            str(row["raw_rank"]),
            digest,
        )
        primary_row = primary_by_identity.get(identity)
        if sha256_text(sequence) != digest or primary_row is None:
            raise ValueError("strict subset is not a verified subset of primary formal12")
        if any(
            str(primary_row[name]) != str(row[name])
            for name in (
                *PRIMARY_IDENTITY_COLUMNS,
                *FORMAL_SCORE_COLUMNS,
                GURUPRASAD_OOD_COLUMN,
            )
        ):
            raise ValueError("strict subset row differs from its primary formal12 row")
        if str(row["toxinpred3_label"]).strip().lower() not in {
            "non-toxin",
            "non-toxic",
            "nontoxic",
        }:
            raise ValueError("strict subset violates the toxicity display gate")
        if str(row["macrel_hemolysis_label"]).strip().lower() != "low":
            raise ValueError("strict subset violates the hemolysis display gate")
        if float(row["guruprasad_instability_index"]) >= 50:
            raise ValueError("strict subset violates the instability display gate")
        strict_sha_by_target.append((str(row["target_key"]), digest))

    selected_primary = tuple(row for row in primary if row["target_key"] == target_key)
    selected_raw = tuple(row for row in raw if row["target_key"] == target_key)
    if not selected_primary or len(selected_primary) != len(selected_raw):
        raise ValueError("requested target has no complete raw/formal12 source split")
    return ValidatedScoreAllBundle(
        receipt_sha256=bundle_receipt_sha256,
        manifest_sha256=manifest_sha,
        storage_uri=storage_uri,
        source_run_id=str(bundle_receipt["run_id"]),
        target_key=target_key,
        all_manifest_files=tuple(sorted(entries.items())),
        primary_rows=selected_primary,
        raw_rows=selected_raw,
        strict_sequence_sha256s=tuple(
            sorted(
                digest
                for strict_target, digest in strict_sha_by_target
                if strict_target == target_key
            )
        ),
        runtime=dict(runtime),
        counts=counts,
    )


__all__ = [
    "FORMAL_SCORE_COLUMNS",
    "GURUPRASAD_OOD_COLUMN",
    "PRIMARY_IDENTITY_COLUMNS",
    "RAW_OCCURRENCE_COLUMNS",
    "ValidatedScoreAllBundle",
    "ValidatedScoreSourceMap",
    "validate_score_all_bundle",
    "validate_score_source_map_receipt",
]
